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
files live" above). No server, no login, nothing to authorize — a human
opens it directly in a browser to see current status. An agent should
never open/read/grep `BOARD.html` for this — read
`project-management/board/epics/*.json` /
`project-management/board/stories/*.json` directly instead (see below).
It's pre-populated with one Epic ("agent-memory-sdk") and one Story per
build step (STEP-1 through STEP-8), each already carrying that step's
summary.

**`BOARD.html` is a generated file — never hand-edit its embedded JSON,
and never read/grep it to check board state either.** (It was hand-edited
directly until 2026-08-05; if you're re-reading an older cached version
of this section, that guidance is stale — see the dated DECISIONS.md
entry.) Its actual source of truth is one small JSON file per record:
`project-management/board/epics/<EPIC-ID>.json` and
`project-management/board/stories/<STORY-ID>.json`. This split exists so
many agents/subagents can update different stories at the same time
without colliding on one large file, and so no agent has to read a
~1700-line file just to check one story's status — see
`project-management/board/README.md` for the full schema and workflow.
Need to know a story's current status, or whether an id is already taken?
Read that one file (or `ls` the directory) — don't open `BOARD.html` for
it. `BOARD.html` itself is for a human to open in a browser, nothing
else touches it directly.

**Every "In BOARD.html, set X's status..." instruction anywhere below in
this file is shorthand for the following, and always has been since the
2026-08-05 restructuring — read it that way wherever it appears, in every
step from here on:**

1. Edit `project-management/board/stories/<STORY-ID>.json` directly: set
   its `"status"` field, and push a `{"date": "YYYY-MM-DD", "text":
   "..."}` entry into its `"comments"` array summarizing what was built
   (same fields, same working agreement as always — at the *start* of a
   step, status → `"In Progress"`; at the *end*, alongside the
   DECISIONS.md append and git commit already required, status →
   `"Done"` plus the comment).
2. Run `make board` (or `python project-management/board/build.py`) to
   regenerate `BOARD.html` from that file.
3. Commit the shard file and the regenerated `BOARD.html` together, in
   the same commit as the rest of the step's work.

Step 2 is also enforced automatically: `make install-hooks` (once per
clone) installs a pre-commit hook that rebuilds and stages `BOARD.html`
for you if a shard changed and step 2 was skipped, and blocks the commit
outright if a shard is invalid. Still do step 2 yourself rather than
relying on it — the hook is a safety net for a forgotten rebuild, not a
substitute for checking your own work.

Refresh `BOARD.html` in a browser any time to see current status.

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
board (not Jira — Jira's MCP connection isn't working). Open it in a
browser to see current status.

BOARD.html ITSELF IS GENERATED — NEVER HAND-EDIT ITS EMBEDDED JSON. Its
source of truth is one file per record: project-management/board/
epics/<EPIC-ID>.json and project-management/board/stories/<STORY-ID>.json
(see project-management/board/README.md for the schema). Wherever any
step below says "in BOARD.html, set X's status to Y and add a comment",
that means: edit project-management/board/stories/X.json (set "status",
push a {"date","text"} entry into "comments"), then run `make board` (or
`python project-management/board/build.py`) to regenerate BOARD.html, and
commit both the shard file and the regenerated BOARD.html together with
the rest of that step's work. Adding a brand-new epic/story that doesn't
exist yet works the same way — create the new file(s) under
project-management/board/, then `make board`.
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

---

# Epic 8 — Conversational ergonomics: a Thread/session API layer

Tracked as `EPIC-8` in [`BOARD.html`](BOARD.html) (Stories `THRD-1` through
`THRD-6`). Distinct from `EPIC-3` (Oracle-inspired, Done) and `EPIC-7`
(fresh 2026 pipeline research, Done) — both already closed other gaps
against Oracle AI Agent Memory (`ORC-1`/`PIPE-4`'s context-card blending,
`PIPE-5`'s erasure, `PIPE-6`'s export/import). This epic is grounded in a
direct, dated review of Oracle's live **How-to Guides**
(`https://docs.oracle.com/en/database/oracle/agent-memory/26.4/agmea/`,
specifically *Store and Search Memory*, *Use Agent Memory with an MCP
Server*, and the *Quick Reference Code Samples* page) — see the
"2026-08-02 — EPIC-8 backlog" entry in `DECISIONS.md` for the full gap
writeup and what's already confirmed at parity or ahead.

Six genuine, purely ergonomic gaps were found — the underlying primitives
(`remember()`, `search()`, scoped repositories) already do the work;
nothing in this epic is a new storage capability:

1. No first-class `Thread` object (`create_thread`/`get_thread`/
   `delete_thread` with cascade).
2. No batch message API (`add_messages`/`get_messages(start, end)`/
   `delete_message`).
3. No thin `add_memory()`/`add_user()`/`add_agent()` convenience wrappers
   over `remember()`.
4. No automatic LLM-driven memory extraction on message ingest.
5. No token-budget-aware thread summary (`get_summary(except_last=,
   token_budget=)`).
6. No raw-text `search()` facade (today's `search()` takes a pre-computed
   embedding only).

Same working agreement as every other epic: read `DECISIONS.md` first,
update it + `BOARD.html` before finishing, commit each story separately.
Same Step 0 philosophy: Db2-only, zero mandatory new infrastructure,
developer-controlled writes by default — every new hook here
(`MemoryExtractor`, `extract_memories=`) is opt-in and must leave today's
default behavior byte-for-byte unchanged when not configured.

## Designed for parallel subagents

**Fully independent — safe to run as four separate subagents/worktrees at
the same time:** `THRD-1`, `THRD-2`, `THRD-3`, `THRD-4`. Each adds new
methods to `MemoryStore` only — no story edits a method another story
touches. To keep parallel diffs additive rather than overlapping, **each
story must append its new method(s) as its own clearly-delimited
banner-comment section at the end of the `MemoryStore` class**, exactly the
pattern `get_context_card()` already uses at `store.py:1258` (`# ------
get_context_card() ... (ORC-1) ------`) — do not interleave a new method
into the middle of an existing section. If running these via separate git
worktrees, expect all four branches to touch `store.py` and `__init__.py`
(new exports) — merge them one at a time; conflicts should be limited to
adjacent-line insertions, not overlapping logic, if the banner-comment
convention is followed.

**Sequenced — depends on another story in this epic:**
- `THRD-5` depends on `THRD-1` (`add_messages()` must exist as the hook
  point for auto-extraction). Do not start `THRD-5` until `THRD-1` is
  merged.
- `THRD-6` depends on **all** of `THRD-1` through `THRD-5` — it's a pure
  composition layer (a `Thread` object that thinly wraps each of their new
  methods bound to one scope). Run it last, against a merged base
  containing all five.

Suggested execution: launch `THRD-1`/`THRD-2`/`THRD-3`/`THRD-4` together
(4 parallel subagents), merge all four, then launch `THRD-5` (1 subagent),
merge, then run `THRD-6` alone.

---

## THRD-1 — Message ingestion primitives: add_messages / get_messages / delete_message

```
Before starting: in BOARD.html, set THRD-1's status to "In Progress".

Oracle's OracleThread exposes add_messages(list[Message|dict]),
get_messages(start=, end=), and delete_message(id) as first-class,
non-embedding-only operations over conversation turns. This SDK has no
equivalent — WorkingMemory rows are the message-equivalent (see
models.py:120's docstring, "Typically created once per agent
message/response pair") but there's no dedicated write/read/delete surface
for them; callers must construct WorkingMemory instances and call
remember()/forget() directly, and there's no start/end slicing over a
thread's turns.

Add three methods to MemoryStore (store.py), each in its own new
banner-comment section appended at the end of the class (do not touch any
existing method):

1. `add_messages(messages: list[dict[str, Any]], scope: MemoryScope) ->
   list[str]` — each dict has a required `content` key and optional
   `role`, `id`, `timestamp`, `metadata` keys (document the exact accepted
   shape in the docstring — a dataclass/TypedDict `Message` type in
   types.py is fine if you prefer strong typing over a raw dict, your
   call, justify the choice in DECISIONS.md). For each message, build a
   WorkingMemory record (content=content, metadata={"role": role,
   **(metadata or {})}, id=id if the caller supplied one — _MemoryBase
   already allows a caller-supplied id via its default_factory, confirm
   this and use it), call remember() with the given scope, and collect the
   returned ids in input order. Embed each message's content via the
   configured embedding_provider exactly like every other remember() path
   already does — no new embedding logic.
2. `get_messages(scope: MemoryScope, start: int = 0, end: int | None =
   None) -> list[WorkingMemory]` — fetch via store.working.list_all()
   (documented today as newest-first), reverse to chronological
   (oldest-first) order, then apply Python list-slice semantics
   `[start:end]` (end=None means "to the end", matching Oracle's
   documented behavior in the Quick Reference page).
3. `delete_message(message_id: str, scope: MemoryScope) -> int` — soft-
   deletes (via the existing working.forget()) the single WorkingMemory
   row matching message_id AND scope; returns 1 if a row was tombstoned, 0
   if no matching row was found (including a message_id that belongs to a
   different scope — do not leak whether the id exists elsewhere).
   Document explicitly in the docstring that this deliberately stays a
   soft-delete (consistent with this SDK's forget()/tombstone lifecycle
   philosophy) rather than Oracle's hard-delete — a documented, deliberate
   divergence, not an oversight. Note in the docstring that (like Oracle's
   own delete_message) this does not cascade to any derived
   facts/profiles produced from this message — only erase_all() does that
   kind of cascade.

## Acceptance Criteria

- `add_messages()` accepts a list of message dicts, returns ids in the
  same order as input, and every returned id round-trips via
  `store.working.get_by_id()`.
- `add_messages()` on an empty list returns `[]` without touching the
  database.
- `get_messages()` returns messages in chronological (oldest-first) order
  regardless of `list_all()`'s underlying storage order.
- `get_messages(start=1, end=3)` returns exactly the same slice Python's
  `list[1:3]` would over the full chronological list; `end=None` returns
  through the end of the list.
- `get_messages()` on a scope with zero messages returns `[]`, not an
  error.
- `delete_message()` returns 1 and the message no longer appears in
  `get_messages()` afterward (still fetchable directly by id only via a
  scope-and-tombstone-aware lookup, i.e. the standard forget() contract).
- `delete_message()` on an id belonging to a *different* scope returns 0
  and does not delete the row.
- Default behavior of every pre-existing MemoryStore method is unchanged
  (no existing test regresses).
- New unit tests added in `tests/test_thrd1_messages.py` covering every
  bullet above, using the existing fake/mock repository pattern (no live
  Db2 required).

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the exact `Message` input shape accepted and confirm
the soft-delete-not-hard-delete decision. In BOARD.html, set THRD-1's
status to "Done" and add a comment summarizing what you built. Then
`git add -A && git commit -m "thrd-1: message ingestion primitives (add_messages/get_messages/delete_message)"`.
```

---

## THRD-2 — add_memory() / add_user() / add_agent() convenience wrappers

```
Before starting: in BOARD.html, set THRD-2's status to "In Progress".

Oracle's memory.add_memory(content, user_id=, agent_id=, thread_id=,
memory_id=), memory.add_user(user_id, profile_text), and
memory.add_agent(agent_id, profile_text) are thin, single-call convenience
wrappers. This SDK's equivalent capability already exists (remember() plus
SemanticFact/EntityProfile model construction) but requires every caller to
build the Pydantic model instance by hand — there is no thin wrapper.

Add three methods to MemoryStore (store.py), in their own new
banner-comment section appended at the end of the class (do not touch
THRD-1's section or any existing method):

1. `add_memory(content: str, scope: MemoryScope, *, memory_id: str | None
   = None, metadata: dict | None = None) -> str` — builds a SemanticFact
   (id=memory_id if given, else the model's default uuid factory) and
   calls remember(record, scope); returns record.id (which, per
   content_hash dedup already in create() — see ENH-2 — may be an
   *existing* row's id if the content already exists in this scope; this
   is correct/expected behavior, not a bug, document it).
2. `add_user(user_id: str, profile_text: str, scope: MemoryScope | None =
   None) -> str` and `add_agent(agent_id: str, profile_text: str, scope:
   MemoryScope | None = None) -> str` — upsert semantics: look up an
   existing EntityProfile for (agent_id, user_id) with no thread_id set
   (matching EntityProfile's own docstring at models.py:198, "Typically
   one row per (agent_id, user_id) pair"); if found, update() its content
   to profile_text (bump version per the existing optimistic-concurrency
   path); if not found, create() a new EntityProfile. Return the
   profile's id. If `scope` is omitted, build one from the given
   user_id/agent_id (document exactly how — likely
   MemoryScope(agent_id=agent_id, user_id=user_id) for add_user, and
   MemoryScope(agent_id=agent_id) for add_agent, since an agent profile
   isn't inherently user-scoped).

## Acceptance Criteria

- `add_memory()` returns an id that round-trips via `store.facts.get_by_id()`.
- Calling `add_memory()` twice with identical content in the same scope
  returns the **same** id both times (content_hash dedup already applies —
  confirm, don't reimplement).
- `add_memory(memory_id="custom_001", ...)` returns exactly `"custom_001"`.
- `add_user()` called twice for the same user_id/agent_id with different
  profile_text results in exactly one EntityProfile row for that scope,
  with the second call's content — not two rows.
- `add_user()`/`add_agent()` called for the first time on a fresh scope
  creates exactly one new row.
- `add_agent()` scope does not require a user_id.
- Default behavior of every pre-existing MemoryStore method is unchanged.
- New unit tests added in `tests/test_thrd2_convenience.py` covering every
  bullet above.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the upsert lookup key used for add_user/add_agent
and the default-scope construction rule when `scope` is omitted. In
BOARD.html, set THRD-2's status to "Done" and add a comment summarizing
what you built. Then
`git add -A && git commit -m "thrd-2: add_memory/add_user/add_agent convenience wrappers"`.
```

---

## THRD-3 — Raw-text search() facade across record types

```
Before starting: in BOARD.html, set THRD-3's status to "In Progress".

Oracle's memory.search(query="text", scope=SearchScope(...),
record_types=["memory"], metadata_filter={...}) takes raw query text and
fans out across whichever record types the caller asks for. This SDK's
BaseRepository.search()/search_chunks() (repositories/base.py:1393) both
require a pre-computed query_embedding and operate on exactly one
repository — every caller must embed manually and pick a single type.

Add a `SearchResult` dataclass to types.py (parallel in shape to
`ErasureReport`/`ContextCard`): `id: str`, `content: str`, `record_type:
str` (one of "working"/"episodic"/"facts"/"profiles"/"procedures"),
`distance: float`, `record: _MemoryBase` (the full model instance).

Add `MemoryStore.search(query: str, scope: MemoryScope, record_types:
list[str] | None = None, max_results: int = 10, metadata_filter: dict |
None = None) -> list[SearchResult]` in its own new banner-comment section
appended at the end of the class:
1. Raise a clear, actionable error if no embedding_provider was configured
   at construction time (this method has no way to embed query text
   without one).
2. Embed `query` once via the configured embedding_provider.
3. `record_types` defaults to all five ("working", "episodic", "facts",
   "profiles", "procedures"); reject unrecognized names with a clear
   error (mirroring ORC-3's "reject unrecognized operator keys" pattern).
4. For each requested type, call that repository's existing search()
   (passing metadata_filter through untouched — ORC-3's operators already
   work here for free) with a generous top_k so cross-type merging has
   enough candidates, wrap each hit in a SearchResult tagged with its
   record_type.
5. Merge all results, sort by ascending distance, truncate to
   max_results.

## Acceptance Criteria

- `search()` with no embedding_provider configured raises a clear
  exception naming the missing configuration, not a generic AttributeError.
- `search("query", scope)` with default record_types returns results from
  more than one repository when relevant matches exist in more than one.
- `search("query", scope, record_types=["facts"])` returns only
  SearchResult entries with record_type == "facts".
- `search("query", scope, record_types=["not_a_real_type"])` raises a
  clear error before touching the database.
- Results are sorted by ascending distance across the merged set (not just
  within each type).
- `len(results) <= max_results` always holds.
- `metadata_filter` passed through to search() is honored (reuse an
  existing ORC-3 test fixture/pattern to confirm).
- Default behavior of every pre-existing MemoryStore method is unchanged.
- New unit tests added in `tests/test_thrd3_search.py` covering every
  bullet above.

Before starting: read DECISIONS.md in full, including the ORC-3 entry for
the metadata_filter operator set. Before finishing: append a dated entry
recording the SearchResult shape and the per-type top_k over-fetch factor
chosen. In BOARD.html, set THRD-3's status to "Done" and add a comment
summarizing what you built. Then
`git add -A && git commit -m "thrd-3: raw-text search() facade across record types"`.
```

---

## THRD-4 — Token-budget-aware thread summary: get_summary()

```
Before starting: in BOARD.html, set THRD-4's status to "In Progress".

Oracle's thread.get_summary(except_last=N, token_budget=N) returns a
plain-text, role-labeled transcript of a thread's messages — a distinct,
simpler mechanism from get_context_card()'s optional LLM-based Summarizer
hook (ORC-1): no LLM call, just a deterministic, budget-truncated raw-text
view. This SDK has no equivalent.

Add a `Summary` dataclass to types.py: `content: str`, `message_count:
int` (messages actually included after except_last/token_budget were
applied), `truncated: bool` (True if token_budget cut the transcript
short).

Add `MemoryStore.get_summary(scope: MemoryScope, except_last: int = 0,
token_budget: int | None = None) -> Summary` in its own new
banner-comment section appended at the end of the class:
1. Fetch chronological messages the same way THRD-1's get_messages()
   does (do not require THRD-1 to be merged first — duplicate the
   minimal list_all()-plus-reverse logic here if THRD-1 isn't available
   yet in your branch; note in DECISIONS.md if you later dedupe this
   against THRD-1's helper once both are merged).
2. If except_last > 0, drop the last N messages from the chronological
   list before formatting.
3. Format each remaining message as `"{role} (-): {content}"` (matching
   the exact format shown in Oracle's Quick Reference example — the "(-)"
   is a literal timestamp placeholder when no real timestamp is tracked;
   if a message has a real timestamp in its metadata, use it in place of
   "-", document which).
4. If token_budget is set, use a simple whitespace-token approximation
   (`len(text.split())`) — not a real tokenizer, document this
   explicitly as an approximation, not a hard dependency on tiktoken or
   similar — and truncate the formatted transcript to fit, setting
   truncated=True. Without a real tokenizer, over-estimating length is
   safer than under-estimating (never exceed budget); justify your exact
   rounding rule in DECISIONS.md.

## Acceptance Criteria

- `get_summary()` with no arguments returns every message in the scope,
  chronologically formatted, truncated=False.
- `get_summary(except_last=1)` excludes exactly the last chronological
  message from the output.
- `get_summary(token_budget=N)` never returns a transcript whose
  whitespace-token count exceeds N, and sets truncated=True whenever
  truncation actually occurred (False if the full transcript already fit).
- `message_count` accurately reflects how many messages appear in
  `content` after both except_last and token_budget are applied.
- `get_summary()` on a scope with zero messages returns an empty-content
  Summary, not an error.
- `except_last` larger than the total message count returns an
  empty-content Summary, not a negative-index error.
- Default behavior of every pre-existing MemoryStore method is unchanged.
- New unit tests added in `tests/test_thrd4_summary.py` covering every
  bullet above.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the exact transcript line format, the token-counting
approximation and its rounding rule, and confirm this is a distinct
mechanism from get_context_card()'s Summarizer hook (cross-reference the
ORC-1 entry). In BOARD.html, set THRD-4's status to "Done" and add a
comment summarizing what you built. Then
`git add -A && git commit -m "thrd-4: token-budget-aware get_summary()"`.
```

---

## THRD-5 — MemoryExtractor: automatic LLM-driven memory extraction on message ingest

```
Before starting: in BOARD.html, set THRD-5's status to "In Progress".
Depends on THRD-1 — do not start until add_messages() is merged.

Oracle's OracleThread.add_messages() automatically extracts durable
memories from recent thread messages via a configured LLM, on by default
(extract_memories=True), with an explicit opt-out. This SDK has no
equivalent — the existing Consolidator (ENH stories) processes raw
working/episodic writes generically, and PIPE-2's IngestResolver
classifies a candidate the *caller* already decided to write; neither is
triggered automatically by add_messages() itself.

Add a `MemoryExtractor` Protocol to types.py, parallel in shape to
`Consolidator`/`Reconciler`/`IngestResolver` (read all three existing
protocols in types.py first — Consolidator at types.py:65, Reconciler at
types.py:236, IngestResolver at types.py:432 — and match their docstring
style exactly, including a comparison paragraph explaining how
MemoryExtractor differs from each of the other three):

    (messages: list[WorkingMemory], scope: MemoryScope) -> list[_MemoryBase]

returning zero or more derived SemanticFact/EntityProfile records to
persist. Ship a `NoOpMemoryExtractor` default (returns `[]` always),
matching the NoOpConsolidator/NoOpReconciler/NoOpIngestResolver pattern
exactly (same file, same style).

Add an optional `memory_extractor: Any | None = None` constructor param on
MemoryStore (defaults to NoOpMemoryExtractor — additive, no existing
param renamed or removed) and an `extract_memories: bool = True` parameter
on THRD-1's `add_messages()` (mirroring Oracle's own per-call escape
hatch). When `extract_memories` is True AND a non-NoOp extractor is
configured: after inserting the messages, call the extractor with the
newly-added WorkingMemory records and scope, then remember() each derived
record through the *existing* remember() path — meaning PIPE-2's
IngestResolver (if also configured) and ENH-2's content-hash dedup already
apply to extracted memories for free; do not duplicate that logic here.
Extractor errors must be caught and logged, never propagated (matching the
Summarizer error-handling pattern in get_context_card() — errors degrade
gracefully, they don't fail the write).

With the default NoOpMemoryExtractor (no real extractor configured),
add_messages()'s behavior must be identical to THRD-1's original
behavior — confirm this with a regression test.

## Acceptance Criteria

- `MemoryExtractor` Protocol and `NoOpMemoryExtractor` added to types.py,
  matching the existing three protocols' docstring conventions.
- With no memory_extractor configured (the default), add_messages()'s
  return value and side effects are byte-for-byte identical to THRD-1's
  pre-THRD-5 behavior — an explicit regression test proves this.
- With a test double MemoryExtractor configured that returns one
  SemanticFact per call, add_messages() results in that fact being
  persisted and retrievable via store.facts.
- add_messages(..., extract_memories=False) skips extraction even when a
  real extractor is configured.
- An extractor that raises is caught and logged; add_messages() still
  returns the message ids successfully (the write itself never fails due
  to an extractor error).
- Extracted records pass through remember() — if an IngestResolver is
  also configured, confirm (via test double) it is invoked for each
  extracted candidate exactly as it would be for any other remember()
  call.
- New unit tests added in `tests/test_thrd5_extraction.py` covering every
  bullet above.

Before starting: read DECISIONS.md in full, including THRD-1's entry.
Before finishing: append a dated entry recording the MemoryExtractor
protocol shape and the exact comparison against Consolidator/Reconciler/
IngestResolver, and confirm the no-extractor-configured default path is
unchanged. In BOARD.html, set THRD-5's status to "Done" and add a comment
summarizing what you built. Then
`git add -A && git commit -m "thrd-5: automatic memory extraction on message ingest via MemoryExtractor"`.
```

---

## THRD-6 — Thread facade: a bound-scope convenience object

```
Before starting: in BOARD.html, set THRD-6's status to "In Progress".
Depends on THRD-1 through THRD-5 — do not start until all five are merged.

Oracle's memory.create_thread(thread_id=, user_id=, agent_id=) returns a
Thread handle whose methods (add_messages, get_messages, delete_message,
add_memory, delete_memory, search, get_summary, get_context_card) are all
pre-bound to that thread's scope, so callers don't repeat
MemoryScope(...) on every call. This SDK has no such object — every method
added in THRD-1..5 (plus the existing get_context_card()) takes an
explicit scope argument every time.

Add a new file `src/agent_memory_sdk/thread.py` with a `Thread` class:
constructed with a `MemoryStore` reference and a bound `MemoryScope`
(must include thread_id). Its methods are thin pass-throughs, binding
`self._scope` automatically:
- `add_messages(messages, extract_memories=True) -> list[str]`
- `get_messages(start=0, end=None) -> list[WorkingMemory]`
- `delete_message(message_id) -> int`
- `add_memory(content, *, memory_id=None, metadata=None) -> str`
- `delete_memory(memory_id) -> int` (thin wrapper over store.facts.forget,
  scoped — mirrors Oracle's thread.delete_memory)
- `search(query, max_results=10, record_types=None, metadata_filter=None)
  -> list[SearchResult]`
- `get_summary(except_last=0, token_budget=None) -> Summary`
- `get_context_card(**kwargs) -> ContextCard` (pass-through to the
  existing MemoryStore.get_context_card(), binding scope)

Add `MemoryStore.create_thread(thread_id: str | None = None, user_id: str
| None = None, agent_id: str | None = None, tenant_id: str | None = None)
-> Thread`: generates a thread_id (uuid) if not given, builds the
MemoryScope, returns a bound Thread. Per the Step 0 "zero mandatory new
infrastructure" principle, prefer a schema-less implementation — a thread
with zero writes simply has no rows yet; document this explicitly as a
known, deliberate limitation (a thread cannot be "re-opened" via
get_thread() until it has at least one message or memory written to it).
If you judge a minimal registry table is actually necessary to make
get_thread() work for a zero-write thread, that's a valid call, but it
requires a new migration and must be justified in DECISIONS.md against
the schema-less alternative — don't add one silently.

Add `MemoryStore.get_thread(thread_id: str, agent_id: str, tenant_id: str
| None = None, user_id: str | None = None) -> Thread`: re-opens an
existing thread by looking up its most recent WorkingMemory row (or, if
using a registry table, the registry) within the given
tenant_id/agent_id/user_id to recover its full scope, then returns a bound
Thread. Raise a clear error (not a bare KeyError) if no thread with that
id can be found in that scope.

**`agent_id` is a required parameter here, not optional, and there is no
"search globally across agents" fallback — do not add one.** Every read
path in this SDK (`get_by_id`, `list_all`, `search`, all in
repositories/base.py) hard-requires `scope.agent_id` via
`_require_agent_id`/`_scope_predicates` — this is the VER-5-audited
isolation boundary ("callers cannot read across scopes by guessing IDs"),
not an incidental gap. A thread_id-only lookup with no agent_id would
require a genuinely unscoped table scan, which this SDK deliberately does
not support anywhere. If the caller doesn't know which agent a thread_id
belongs to, that's a caller-side bookkeeping problem (store the agent_id
alongside the thread_id, the same way any other scoped id must be
tracked) — it is not this method's job to solve by relaxing the
isolation boundary. (This corrects an earlier, infeasible version of this
spec that suggested an optional `scope_hint` with a global fallback —
see the dated DECISIONS.md entry recording this correction.)

Add `MemoryStore.delete_thread(scope: MemoryScope) -> ErasureReport`:
requires scope.thread_id to be set (raise otherwise); this can be a
one-line call to the existing `self.erase_all(scope)` (PIPE-5) — erase_all
already hard-deletes every matching row across all five repositories plus
memory_chunks for the given scope, which is exactly Oracle's documented
delete_thread cascade (thread + messages + memories + chunk rows) done
already. Confirm this reuse works before writing any new deletion logic;
do not duplicate erase_all's loop.

Export `Thread` from `agent_memory_sdk/__init__.py`.

## Acceptance Criteria

- `store.create_thread(user_id="u1", agent_id="a1")` returns a Thread
  whose `.thread_id` is a non-empty string, and whose scope has
  user_id="u1"/agent_id="a1".
- `thread.add_messages([...])` followed by `thread.get_messages()`
  round-trips without the caller passing scope anywhere.
- `store.get_thread(existing_thread_id, agent_id="a1")` returns a Thread
  whose scope matches the original create_thread() call, after at least
  one message has been written to it.
- `store.get_thread("nonexistent_id", agent_id="a1")` raises a clear,
  actionable error (not a bare KeyError).
- `store.get_thread(existing_thread_id, agent_id="wrong-agent")` (a real
  thread, but the wrong agent_id) also raises the same not-found error —
  it must not leak whether the thread exists under a different agent_id,
  matching the isolation boundary's existing "cannot read across scopes by
  guessing IDs" contract.
- `store.delete_thread(scope)` with no thread_id set on scope raises
  before touching the database.
- `store.delete_thread(scope)` with thread_id set hard-deletes every row
  for that scope (reuse erase_all — confirm via its existing test
  coverage pattern) and returns an ErasureReport with total_deleted > 0
  when messages/memories existed.
- `thread.search(...)`, `thread.get_summary(...)`, and
  `thread.get_context_card(...)` each return results identical to calling
  the equivalent MemoryStore method directly with the Thread's bound
  scope (a direct-call-vs-thread-call equivalence test for each).
- `Thread` is importable as `from agent_memory_sdk import Thread`.
- New unit tests added in `tests/test_thrd6_thread.py` covering every
  bullet above.

Before starting: read DECISIONS.md in full, including all five prior
THRD entries. Before finishing: append a dated entry recording whether you
chose the schema-less or registry-table approach for get_thread() (and
why), and confirm delete_thread() reuses erase_all() without duplicating
its logic. Update section 1 (system overview) of ARCHITECTURE.md to add
the Thread box if it's a large enough addition to warrant one — your call,
note the decision either way. In BOARD.html, set THRD-6's status to "Done"
and add a comment summarizing what you built. Then
`git add -A && git commit -m "thrd-6: Thread facade object (create_thread/get_thread/delete_thread)"`.
```

---

## EPIC-8 addendum — full API Reference review adds THRD-7..10

`THRD-1` through `THRD-6` above were scoped from Oracle's How-to Guides and
Quick Reference page. A follow-up review of Oracle's full **API Reference**
(`docs.oracle.com/en/database/oracle/agent-memory/26.4/agmea/api/index.html`
— `OracleAgentMemory`, the `Record` taxonomy, `Scope`/`SearchScope`, and
`OracleThread`) found four more genuine gaps, added below as `THRD-7`
through `THRD-10`, and — just as importantly — a longer list of things
this review deliberately did **not** turn into stories. See the "2026-08-02
— EPIC-8 addendum" entry in `DECISIONS.md` for the full writeup; the short
version:

**Not built, on purpose:**
- No new `GuidelineRecord`/`FactRecord`/`PreferenceRecord`-style tables —
  the first two already map onto `SemanticFact`/`ProceduralMemory`; the
  third is a `metadata` tag away from `SemanticFact`, not worth a 6th
  migration.
- `THRD-5` keeps its opt-in, `NoOpMemoryExtractor`-by-default design —
  Oracle's `extract_memories=True`-by-default, fail-without-an-LLM
  contract directly contradicts this SDK's own "developer-controlled
  writes" positioning from the competitive analysis.
- No copy of Oracle's eight token-budget/frequency constructor knobs —
  `ENH-4`'s `consolidate_every_n` already generalizes "don't call the LLM
  on every write."
- No pluggable alternate storage backend — re-litigates the Step 0
  "Database: Db2 LUW" decision.
- `THRD-9` (below) is intentionally **not** a blanket `_async` twin of
  every method — only the handful that actually call an LLM/embedder.
- `THRD-4`'s `get_summary()` stays deterministic/free even though Oracle's
  is LLM-backed — `get_context_card()`'s `Summarizer` hook already covers
  the LLM-narrative-summary use case; a second one would be redundant.

**Risk note:** `THRD-10` is the highest-blast-radius story in this epic —
it's the closest anything here comes to `_scope_predicates()`, the
function `VER-5` hand-audited for cross-tenant leakage. It must stay
scoped to `THRD-3`'s new `search()` facade only. Read the full risk note
in the `DECISIONS.md` addendum entry before starting it.

`THRD-7` and `THRD-8` are independent of everything else in this epic and
of each other — safe to run in parallel with `THRD-1`..`THRD-4` or with
each other. `THRD-9` depends on `THRD-1`, `THRD-3`, and `THRD-5` (it wraps
their sync methods). `THRD-10` depends on `THRD-3` only.

---

## THRD-7 — Cascading identity-scoped delete: delete_user() / delete_agent()

```
Before starting: in BOARD.html, set THRD-7's status to "In Progress".

Oracle's OracleAgentMemory.delete_user(user_id, cascade=True) and
delete_agent(agent_id, cascade=True) take a single identifier and cascade
through every thread, message, memory, and profile owned by that identity
— a broader, more convenient entry point than PIPE-5's erase_all(scope),
which requires the caller to already hold a fully-formed MemoryScope.

Be honest about a real architectural difference before building this:
Oracle's UserProfileRecord is unscoped (a user identity can span multiple
agents). This SDK's EntityProfile.agent_id is a required, non-nullable
field (models.py:69, inherited from _MemoryBase) — a "user" in this SDK
only ever exists within one agent's scope. Do NOT attempt to replicate
Oracle's cross-agent global identity model (that would require making
agent_id nullable across five tables, a real schema change not justified
by this story). Instead, build the narrower, honest version: cascade
within one agent_id (required parameter), and document this constraint
explicitly and prominently in the docstring — this is a deliberate,
narrower reinterpretation, not a bug to fix later.

Add two methods to MemoryStore, in their own new banner-comment section:
- `delete_user(user_id: str, agent_id: str, tenant_id: str | None = None,
  cascade: bool = True) -> ErasureReport` — when cascade=True, build
  MemoryScope(tenant_id=tenant_id, agent_id=agent_id, user_id=user_id)
  and call self.erase_all(scope) directly (reuse — do not duplicate its
  per-table loop). When cascade=False, only remove the matching
  EntityProfile row(s) for that (agent_id, user_id) via
  store.profiles.forget() or an equivalent hard-delete — document exactly
  which (soft vs hard) and why, matching forget()'s existing tombstone
  semantics unless you have a specific reason to diverge.
- `delete_agent(agent_id: str, tenant_id: str | None = None, cascade:
  bool = True) -> ErasureReport` — same pattern, scope has no user_id.

## Acceptance Criteria

- `delete_user(cascade=True)` removes every row (all five tables plus
  chunks) matching that (agent_id, user_id) scope — confirm via
  erase_all's own existing test pattern, reused not reinvented.
- `delete_user(cascade=False)` removes only the EntityProfile row(s) for
  that scope; working/episodic/facts/procedures rows for that user remain.
- `delete_agent(cascade=True)` removes every row matching that agent_id
  across the whole agent (no user_id predicate), including all its users'
  data.
- Both methods require agent_id as a real (non-optional) parameter — the
  docstring explicitly states why (no cross-agent identity in this SDK)
  rather than silently accepting an ambiguous call.
- Calling either on an identity with zero existing rows returns an
  ErasureReport with total_deleted == 0, not an error.
- New unit tests added in `tests/test_thrd7_identity_delete.py`.

Before starting: read DECISIONS.md in full, including the PIPE-5 and
EPIC-8-addendum entries. Before finishing: append a dated entry explicitly
recording the agent-scoped-only limitation versus Oracle's cross-agent
identity model, and the soft-vs-hard-delete choice for cascade=False. In
BOARD.html, set THRD-7's status to "Done" and add a comment summarizing
what you built. Then
`git add -A && git commit -m "thrd-7: cascading delete_user()/delete_agent()"`.
```

---

## THRD-8 — Generic table-agnostic delete_memory(id)

```
Before starting: in BOARD.html, set THRD-8's status to "In Progress".

Oracle's client-level OracleAgentMemory.delete_memory(memory_id) deletes
"a memory-like record (memory, fact, preference, or guideline)" by id
alone — the caller never has to know which underlying table the id lives
in. This SDK's forget() facade (store.py:692) requires an explicit
memory_type argument today.

Add `MemoryStore.delete_memory(memory_id: str, scope: MemoryScope) -> int`
in its own new banner-comment section: try store.facts, store.profiles,
store.procedures (the three "durable memory-like" repositories — NOT
store.working/store.episodic, which are messages/raw-turns, a
deliberately different category, matching Oracle's own memory-vs-message
distinction) via forget(memory_id, scope) in that order, stopping at the
first one that reports a match (returns True). Return 1 if any repository
tombstoned a row, 0 if none did. Unlike Oracle's global-by-id lookup, this
method still requires scope (this SDK's forget() always does, per VER-5's
audited scoping discipline) — document this as a deliberate, consistent
difference, not a limitation to fix.

## Acceptance Criteria

- `delete_memory(id, scope)` on an id that lives in store.facts returns 1
  and the fact is subsequently absent from list_all()/search().
- Same for an id living in store.profiles, and for store.procedures.
- `delete_memory(id, scope)` on an id that only exists in
  store.working/store.episodic returns 0 (messages/raw-turns are
  deliberately out of scope for this method — use delete_message from
  THRD-1 for those).
- `delete_memory(id, scope)` on a nonexistent id, or an id belonging to a
  different scope, returns 0.
- Only one repository's forget() actually mutates state per call — a test
  confirms the search stops at the first match rather than calling
  forget() on all three regardless.
- New unit tests added in `tests/test_thrd8_delete_memory.py`.

Before starting: read DECISIONS.md in full, including the EPIC-8-addendum
entry. Before finishing: append a dated entry recording the exact
try-order across the three repositories and confirming the
messages-are-excluded design choice. In BOARD.html, set THRD-8's status to
"Done" and add a comment summarizing what you built. Then
`git add -A && git commit -m "thrd-8: generic table-agnostic delete_memory(id)"`.
```

---

## THRD-9 — Async facade for the LLM/embedder-calling entry points

```
Before starting: in BOARD.html, set THRD-9's status to "In Progress".
Depends on THRD-1, THRD-3, and THRD-5 — do not start until all three are
merged (this story wraps their synchronous methods).

Oracle pairs nearly every method with an `_async` twin (search_async,
add_messages_async, get_context_card_async, get_summary_async, on both
OracleAgentMemory and OracleThread), and specifically calls out *why* for
the LLM-backed ones: "may perform remote network I/O." This SDK is fully
synchronous today, which is a real friction point for async agent
frameworks (LangGraph, and this SDK's own PIPE-3 Microsoft Agent Framework
adapter, whose ContextProvider.before_run/after_run are async methods).

Do NOT blanket-wrap every MemoryStore method — see the EPIC-8-addendum
DECISIONS.md entry for why that's explicitly rejected (most methods are
plain Db2 round-trips with no meaningfully latency-sensitive I/O beyond
the DB call itself). Scope this to exactly the methods that call an LLM or
embedder: `search()` (embeds the query text), `add_messages()` when a real
MemoryExtractor is configured (THRD-5), and `get_context_card()` when a
real Summarizer is configured (ORC-1/PIPE-4). Add `search_async()`,
`add_messages_async()`, and `get_context_card_async()` to MemoryStore, in
their own new banner-comment section, each implemented as a thin
`asyncio.to_thread(self.<sync_method>, ...)` wrapper — no new business
logic, no duplicated code paths, so the sync and async versions can never
drift in behavior. If PIPE-3's agent_framework.py adapter currently has
its own ad hoc asyncio.to_thread wrapping around any of these three calls,
check it and simplify it to call the new *_async methods instead — do not
leave two different wrapping mechanisms in the codebase for the same
methods.

## Acceptance Criteria

- `await store.search_async(...)` returns identical results to
  `store.search(...)` called synchronously with the same arguments, for
  the same underlying data.
- Same equivalence test for `add_messages_async()` and
  `get_context_card_async()`.
- All three async methods actually release the event loop during the
  wrapped call (a test using asyncio's event loop, confirming another
  coroutine can run concurrently — not just that the method is
  technically declared `async def`).
- No `_async` twin is added for any method outside this story's three
  (confirm by checking the diff doesn't touch remember/forget/list_all/
  purge_expired/erase_all/export_scope/import_scope).
- If PIPE-3's adapter had ad hoc async wrapping around any of these three
  calls, it now calls the new *_async methods instead — no duplicate
  wrapping logic remains.
- New unit tests added in `tests/test_thrd9_async.py`.

Before starting: read DECISIONS.md in full, including the PIPE-3 and
EPIC-8-addendum entries. Before finishing: append a dated entry recording
exactly why these three methods (and no others) were chosen, and confirm
whether PIPE-3's adapter needed updating. In BOARD.html, set THRD-9's
status to "Done" and add a comment summarizing what you built. Then
`git add -A && git commit -m "thrd-9: async facade for LLM/embedder-calling methods"`.
```

---

## THRD-10 — Fuzzy vs. exact per-dimension scope matching in search(), including unscoped-only queries

```
Before starting: in BOARD.html, set THRD-10's status to "In Progress".
Depends on THRD-3 — do not start until it is merged.

**Read the "Risk note on THRD-10" in the EPIC-8-addendum DECISIONS.md
entry before writing any code.** This is the highest-blast-radius story in
this epic.

Oracle's Scope/SearchScope model distinguishes three states per dimension
(user_id/agent_id/thread_id): omitted (resolves to an operation-specific
default), explicit None ("unscoped on this dimension" — matches only NULL
rows when paired with exact_*_match=True), and a concrete id. This SDK's
MemoryScope has no such distinction — None always means "don't filter on
this dimension at all" (repositories/base.py:193 _scope_predicates), so
there is currently no way to search for "only records with no user_id."

This story does NOT touch _scope_predicates(), MemoryScope, or any other
already-shipped method — it adds new, additive-only optional parameters
to THRD-3's search() facade alone, implemented as an extra filtering layer
on top of the existing per-repository search() calls (e.g., post-filter
the candidate set, or add a scoped WHERE fragment specific to this new
method's own query construction — your call, but it must not alter
_scope_predicates()'s existing behavior for any other caller).

Add to MemoryStore.search() (THRD-3): `exact_agent_match: bool = True`,
`exact_thread_match: bool = True` (default to exact — this SDK's existing
behavior everywhere else is always-exact, so default-exact keeps this
addition's default behavior consistent with the rest of the SDK, a
deliberate divergence from Oracle's own thread-search default of
exact_thread_match=False — document why). When exact_agent_match=False,
the agent_id filter is dropped entirely for this call (unconstrained,
matching Oracle's "False leaves that dimension unconstrained" semantics).
When an explicit agent_id/thread_id of None is passed together with its
exact_*_match=True, match only rows where that column IS NULL — this is
the new "unscoped-only" capability that doesn't exist anywhere in the SDK
today.

## Acceptance Criteria

- Default behavior of `search()` (no new parameters passed) is
  byte-for-byte identical to THRD-3's original behavior — an explicit
  regression test proves this.
- `search(..., exact_agent_match=False)` returns results regardless of
  agent_id (still respecting the SDK's mandatory `scope.agent_id`-required
  contract for the base MemoryScope passed in — this only affects the new
  optional override behavior, not the required base scope; document
  precisely how the two interact).
- `search(..., thread_id=None, exact_thread_match=True)` returns only
  records whose thread_id column is genuinely NULL, not records where
  thread_id was simply unfiltered.
- A cross-scope isolation test (mirroring VER-5's existing pattern)
  confirms this story introduces no new cross-tenant leakage — run it
  against at least two different agent_id/tenant_id combinations.
- The diff for this story touches only MemoryStore.search()
  (THRD-3's method) and its own new helper(s) — a reviewer can confirm by
  checking repositories/base.py's _scope_predicates() is unchanged.
- New unit tests added in `tests/test_thrd10_scope_matching.py`, including
  explicit cross-scope-isolation coverage, not just happy-path coverage.

Before starting: read DECISIONS.md in full, including THRD-3's entry, the
VER-5 entry, and the EPIC-8-addendum risk note. Before finishing: append a
dated entry recording the exact default-exact-match divergence from
Oracle's default-fuzzy-thread-match choice and why, and explicitly confirm
_scope_predicates()/MemoryScope were not modified. In BOARD.html, set
THRD-10's status to "Done" and add a comment summarizing what you built.
Then
`git add -A && git commit -m "thrd-10: fuzzy/exact per-dimension scope matching in search()"`.
```

---

# Epic 9 — Software design documentation package (project-approval grade)

Tracked as `EPIC-9` in [`BOARD.html`](BOARD.html) (Stories `SDD-1` through
`SDD-12`). Different in kind from every epic above: `EPIC-1`..`EPIC-8`
build code; this epic builds the formal design-documentation package a
project-approval / architecture-review process needs — system
architecture, data architecture, interface specification, flow diagrams,
security design, data governance, extensibility architecture,
non-functional/capacity design, deployment & operations, testing
strategy, and a risk register. See the "2026-08-02 — EPIC-9 backlog"
entry in `DECISIONS.md` for the full rationale.

**Hard rule for every story below: no competitor or reference-implementation
names.** Every prior epic's stories cite an external system (Oracle AI
Agent Memory, Mem0, Azure Cosmos DB's Agent Memory Toolkit, Microsoft
Agent Framework) as the reason a feature exists. This epic's documents
must not — describe this system on its own technical merits only, sourced
from this repository's own code, `DECISIONS.md`, `ARCHITECTURE.md`, and
`BENCHMARKS.md`. (Naming LangChain/OpenAI Agents SDK/MCP/Microsoft Agent
Framework as *integration targets* this SDK ships adapters for is fine —
that's a technical dependency, not a competitive comparison. Citing them
as the *inspiration* for a design choice is not.)

New documents live under a new `project-management/design/` directory —
**not** more content folded into `ARCHITECTURE.md`, which stays the
single living current-state summary per its own closing section. Each
story below owns exactly one new file (or a clearly split pair, noted
where relevant) so eleven of the twelve stories can run as fully
independent parallel subagents with zero shared-file edits — a stronger
isolation guarantee than any prior epic, since these are new files, not
edits to existing shared modules.

## Designed for parallel subagents

**Fully independent — safe to run as eleven simultaneous
subagents/worktrees:** `SDD-1` through `SDD-11`. Each reads existing code
and docs (read-only with respect to the rest of the repo) and writes
exactly one new file under `project-management/design/`.

**Sequenced last:** `SDD-12` (the package index/README) depends on all
eleven being merged — it cross-references files that must already exist.

Suggested execution: launch all eleven of `SDD-1`..`SDD-11` together,
merge them, then run `SDD-12` alone.

---

## SDD-1 — System Architecture & Component Design Document

```
Before starting: in BOARD.html, set SDD-1's status to "In Progress".

Write project-management/design/01-system-architecture.md: the top-level
architecture document a reviewer reads first. Ground it in
src/agent_memory_sdk/'s actual module layout (models.py, types.py,
repositories/ [base.py + six per-type repos + chunks.py], store.py,
db/connection.py + db/migrate.py, adapters/ [four adapters]) and
ARCHITECTURE.md section 1, but write this as a standalone, self-contained
document — do not assume the reader has read ARCHITECTURE.md.

Required sections:
1. Purpose and scope of the system (one paragraph, no comparison to any
   external system).
2. Layered architecture diagram (Mermaid `graph TD`): models -> repositories
   -> store (facade) -> adapters, plus db/ as a cross-cutting layer feeding
   repositories. Show which layers depend on which (one-directional).
3. Design principles, each with its concrete rationale pulled from this
   repo's own history (not from any external inspiration): normalized
   per-type tables over one polymorphic table (and the specific technical
   reason: a NOT NULL vector column per type, differently-shaped
   embeddings per type), pluggable-protocol extensibility
   (Consolidator/Reconciler/IngestResolver/Summarizer, forward-reference
   SDD-7 for the full treatment), synchronous-by-default processing model,
   mandatory scope predicates on every query.
4. Component responsibility table: one row per module, one-sentence
   responsibility, primary public entry point(s).
5. Technology stack and why: Python 3.10+, Pydantic v2 models, IBM Db2 LUW
   (VECTOR type + VECTOR_DISTANCE + CREATE VECTOR INDEX), hatchling build
   backend — each with the concrete technical reason already recorded in
   DECISIONS.md's foundational entries, restated here in the reviewer's
   voice rather than the build-log voice.

## Acceptance Criteria

- File exists at project-management/design/01-system-architecture.md.
- Contains at least one valid Mermaid diagram (renders without syntax
  errors — verify by checking Mermaid fence syntax matches
  ARCHITECTURE.md's existing diagrams' style).
- Every module named in the component responsibility table actually
  exists at the stated path (cross-check against the real file tree).
- Zero mentions of any external agent-memory product/vendor by name.
- Self-contained: a reader who has never opened ARCHITECTURE.md can
  follow it without needing to cross-reference that file.

Before starting: read DECISIONS.md's foundational Step 0 entries in full.
Before finishing: append a dated entry noting the file was created and
confirming no external product references were introduced. In
BOARD.html, set SDD-1's status to "Done" and add a comment. Then
`git add -A && git commit -m "sdd-1: system architecture and component design document"`.
```

---

## SDD-2 — Data Architecture & Schema Design Document

```
Before starting: in BOARD.html, set SDD-2's status to "In Progress".

Write project-management/design/02-data-architecture.md. Ground it in the
six real migration files (src/agent_memory_sdk/db/migrations/0001_*.sql
through 0006_*.sql) and models.py — read every migration file directly,
do not reconstruct the schema from memory.

Required sections:
1. Full entity-relationship diagram (Mermaid `erDiagram`) covering all
   seven tables: schema_migrations, working_memory, episodic_memory,
   semantic_facts, entity_profiles, procedural_memory, memory_chunks —
   every column, type, and nullability, sourced directly from the six
   migration files.
2. Per-table column dictionary: table name, column name, type, nullable
   Y/N, default, one-sentence purpose — for every column in every table.
3. Indexing strategy: every CREATE VECTOR INDEX and every supporting
   scope-column index, with the distance metric chosen per table and why
   (pull the metric-choice rationale from the migration files' comments
   and the matching DECISIONS.md Step 2 entry).
4. Migration history table: version, filename, one-line summary of what
   it added, in order 0001 through 0006.
5. Data lifecycle state diagram (Mermaid `stateDiagram-v2`): active ->
   tombstoned (deleted_at set, via forget()) -> purged (hard-deleted, via
   purge_expired()); active -> superseded (superseded_at set, via
   reconcile(), semantic_facts only) -> excluded from reads; active ->
   erased (hard-deleted immediately, via erase_all(), bypassing the
   tombstone state entirely) — show erase_all as a direct edge from
   active, not routed through tombstoned.

## Acceptance Criteria

- File exists at project-management/design/02-data-architecture.md.
- The erDiagram includes all seven tables with correct column
  types/nullability, verified against the actual migration SQL, not
  inferred from models.py alone (models.py and the DDL must agree; if
  they don't, flag the discrepancy explicitly rather than silently
  picking one).
- The state diagram has a distinct, separate edge for erase_all()'s
  direct active-to-erased transition (not merged with the tombstone
  path) — this is the single most load-bearing lifecycle distinction in
  the whole schema and must not be flattened.
- Migration history table has exactly six rows, one per real migration
  file, in filename order.
- Zero external product references.

Before starting: read DECISIONS.md's Step 2, ENH-1/2/3, ORC-2, and
PIPE-5 entries in full (each touched schema). Before finishing: append a
dated entry. In BOARD.html, set SDD-2's status to "Done" and add a
comment. Then
`git add -A && git commit -m "sdd-2: data architecture and schema design document"`.
```

---

## SDD-3 — API & Interface Design Specification

```
Before starting: in BOARD.html, set SDD-3's status to "In Progress".

Write project-management/design/03-api-interface-spec.md: a formal
interface-contract document, not a tutorial. Ground it in store.py (every
public MemoryStore method), repositories/base.py (the shared repository
contract), and types.py (every pluggable protocol).

Required sections:
1. MemoryScope contract: field table (tenant_id/agent_id/user_id/
   thread_id), the hierarchy rule, and the "agent_id required on every
   call" invariant stated as a formal precondition.
2. MemoryStore method contract table — one row per public method
   (remember, forget, purge_expired, erase_all, export_scope,
   import_scope, reconcile, get_context_card, plus any THRD-* methods
   already merged at the time this story runs): signature, preconditions,
   postconditions, exceptions raised, idempotency (is calling it twice
   with the same input safe/equivalent?).
3. Repository base contract: the CRUD+search operations every one of the
   six per-type repositories implements identically (create, get_by_id,
   list_all, update, forget, purge_expired, erase_all, search), stated
   once as a shared contract rather than six times.
4. Extension interface contract table: one row per pluggable protocol
   (Consolidator, Reconciler, IngestResolver, Summarizer, and
   MemoryExtractor if THRD-5 has landed) — exact callable shape, when the
   store invokes it, what it must return, its error-handling contract
   (does an exception propagate or get caught-and-logged?), and its
   NoOp-default behavior.

## Acceptance Criteria

- File exists at project-management/design/03-api-interface-spec.md.
- Every method in the MemoryStore contract table has its exception list
  cross-checked against the actual `raise` statements in store.py for
  that method, not assumed.
- Every protocol in the extension table states its error-handling
  contract explicitly and correctly (verify against store.py's actual
  try/except around each hook, e.g. Summarizer/MemoryExtractor errors are
  caught-and-logged, not propagated — confirm this is actually true in
  code before asserting it in the document).
- Zero external product references.

Before starting: read DECISIONS.md in full for every protocol's origin
story. Before finishing: append a dated entry. In BOARD.html, set SDD-3's
status to "Done" and add a comment. Then
`git add -A && git commit -m "sdd-3: API and interface design specification"`.
```

---

## SDD-4 — Sequence & Flow Diagrams for Core Operations

```
Before starting: in BOARD.html, set SDD-4's status to "In Progress".

Write project-management/design/04-sequence-flows.md. ARCHITECTURE.md
sections 4-7 already describe remember()/search()/metadata-filter/erasure
in prose with partial diagrams — this document supersedes none of that
(don't edit ARCHITECTURE.md) but goes further: full Mermaid `sequenceDiagram`
blocks, plus coverage of flows ARCHITECTURE.md doesn't yet have.

Required sequence diagrams (one Mermaid sequenceDiagram block each, with a
short prose walkthrough above each):
1. remember() full write path: caller -> MemoryStore.remember() ->
   IngestResolver branch (if configured: search() for similar candidates,
   classify ADD/UPDATE/DELETE/NOOP) -> repository.create() -> chunking
   gate (if content exceeds threshold) -> Consolidator trigger (if
   configured and consolidate_every_n cadence hit).
2. search() read path: caller -> MemoryStore/repository.search() -> scope
   predicate construction -> vector distance ranking -> (if hybrid=True)
   keyword scoring + RRF fusion -> (if search_chunks) chunk-table
   two-step ID-then-full-row resolution -> return.
3. erase_all() compliance cascade: caller -> MemoryStore.erase_all() ->
   loop over all five per-type repositories' erase_all() -> ChunkRepository
   .erase_by_scope() -> ErasureReport assembly.
4. export_scope()/import_scope() round-trip: export as a generator over
   all six tables with _type tagging, import as per-record scope
   validation then per-type create().
5. reconcile() supersession flow: fetch non-superseded candidates ->
   Reconciler classification -> supersede() marking the loser row.

## Acceptance Criteria

- File exists at project-management/design/04-sequence-flows.md.
- All five sequence diagrams present, each a valid Mermaid
  sequenceDiagram block.
- Each diagram's branches (the "if configured" / "if enabled" points)
  are actually present as alt/opt blocks in the Mermaid syntax, not
  flattened into a single linear happy path — the conditional branching
  is the entire point of documenting these flows.
- Cross-checked against the real code path for each flow (store.py's
  actual method bodies), not reconstructed from the earlier prose
  descriptions alone.
- Zero external product references.

Before starting: read DECISIONS.md in full for PIPE-1 (hybrid/RRF),
PIPE-2 (IngestResolver), PIPE-5 (erase_all), PIPE-6 (export/import), and
ENH-3 (reconciliation) entries — this story's five diagrams map directly
onto those five stories' mechanisms. Before finishing: append a dated
entry. In BOARD.html, set SDD-4's status to "Done" and add a comment.
Then
`git add -A && git commit -m "sdd-4: sequence and flow diagrams for core operations"`.
```

---

## SDD-5 — Security & Data Isolation Design Document

```
Before starting: in BOARD.html, set SDD-5's status to "In Progress".

Write project-management/design/05-security-design.md. Ground it in the
VER-5 DECISIONS.md entry (the hand-audit of every SQL-construction path),
repositories/base.py's `_scope_predicates`/`_require_agent_id`, and the
PH-4 DECISIONS.md entry (dependency/static scanning).

Required sections:
1. Multi-tenant isolation boundary: the scope hierarchy
   (tenant_id/agent_id/user_id/thread_id) restated as a formal security
   control, not just a data model — state explicitly what it prevents
   (a caller in one scope reading/writing another scope's rows even with
   a known row id) and where it's enforced (every _scope_predicates()
   call site).
2. SQL construction and injection posture: parameterized-query discipline
   (every value bound, never string-interpolated) as the primary control;
   enumerate every `# nosec` annotation in the codebase (grep for it) with
   its justification restated from VER-5, not just "trust the comment."
3. Dependency and static-analysis posture: pip-audit + bandit as
   documented in PH-4, what's in the CI security job (ci.yml's `security`
   job), and the suppression-must-be-justified policy already established.
4. Credential and configuration handling: environment-variable-only
   credential passing (.env.example, DB2_UID/DB2_PWD), no secrets ever
   constructed into SQL text, DB2_SECURITY=SSL as the transport-encryption
   opt-in for untrusted networks.
5. Threat model (a short, honest STRIDE-lite table): threats explicitly
   in scope for this SDK's own code (cross-tenant data leakage via a
   missing scope predicate, SQL injection via an unparameterized value)
   versus threats explicitly out of scope / delegated elsewhere (network
   transport encryption is Db2's/the deployment environment's
   responsibility; physical/administrative database access control is
   the operator's responsibility) — be explicit about the boundary, don't
   imply this SDK solves problems it doesn't.

## Acceptance Criteria

- File exists at project-management/design/05-security-design.md.
- Every `# nosec` annotation actually present in the codebase (verify via
  `grep -rn "nosec" src/`) is enumerated with its real justification, and
  the count in the document matches the real count in code.
- The threat model explicitly separates in-scope from delegated/
  out-of-scope threats — a reviewer must not come away believing this
  document claims coverage (e.g. network encryption) it doesn't.
- Zero external product references.

Before starting: read DECISIONS.md's VER-5 and PH-4 entries in full.
Before finishing: append a dated entry. In BOARD.html, set SDD-5's status
to "Done" and add a comment. Then
`git add -A && git commit -m "sdd-5: security and data isolation design document"`.
```

---

## SDD-6 — Data Governance, Retention & Compliance Design Document

```
Before starting: in BOARD.html, set SDD-6's status to "In Progress".

Write project-management/design/06-data-governance.md. Ground it in the
three-tier deletion model already built (forget/purge_expired/erase_all —
store.py, PIPE-5's ErasureReport in types.py) and the confidence/
content-hash mechanisms (ENH-1/ENH-2).

Required sections:
1. The three-tier deletion policy, formalized as governance policy rather
   than implementation notes: forget() (routine, soft, reversible),
   purge_expired() (maintenance hard-delete of already-tombstoned rows),
   erase_all() (compliance-grade, irreversible, immediate, bypasses the
   tombstone state entirely) — with an explicit statement of which one a
   data-subject erasure request should invoke (erase_all) and why the
   other two are insufficient for that purpose on their own.
2. ErasureReport as an audit-record specification: exact schema
   (rows_deleted per table, total_deleted, erased_at), and a statement of
   what evidentiary claim it supports ("N rows were removed from these M
   tables at this UTC timestamp") versus what it does not claim (it is
   not proof of secure erasure at the storage-media level — that is the
   underlying database/infrastructure's responsibility).
3. Retention policy: expires_at/TTL as a per-row, caller-set policy (not
   a system-wide default), and purge_expired()'s role as the enforcement
   mechanism — note explicitly that purge_expired() must be invoked
   (script or cron) and is not automatic, so retention is only as
   effective as the operator's own scheduling.
4. Data portability: export_scope()/import_scope() as the data-subject
   portability mechanism, and its documented limitation (this SDK's own
   format, not a cross-system interchange standard).
5. Data-quality governance: confidence scoring (0.0-1.0 grounding
   certainty) and content-hash write-time dedup, both stated as
   governance controls over what's allowed to accumulate in the store,
   not just features.

## Acceptance Criteria

- File exists at project-management/design/06-data-governance.md.
- Section 1 explicitly and correctly states erase_all() is the only one
  of the three that bypasses deleted_at/expires_at entirely — verify
  against store.py's actual erase_all()/purge_expired() implementations
  before asserting this.
- Section 2 is honest about ErasureReport's evidentiary limits (does not
  overclaim secure-erasure guarantees the SDK cannot actually provide).
- Zero external product references.

Before starting: read DECISIONS.md's PIPE-5, PIPE-6, ENH-1, and ENH-2
entries in full. Before finishing: append a dated entry. In BOARD.html,
set SDD-6's status to "Done" and add a comment. Then
`git add -A && git commit -m "sdd-6: data governance, retention, and compliance design document"`.
```

---

## SDD-7 — Extensibility & Integration Architecture Document

```
Before starting: in BOARD.html, set SDD-7's status to "In Progress".

Write project-management/design/07-extensibility-architecture.md. Ground
it in types.py (every protocol) and adapters/ (all four adapter modules).

Required sections:
1. The pluggable-protocol pattern as a named architectural pattern: state
   the common shape shared by Consolidator, Reconciler, IngestResolver,
   Summarizer, and MemoryExtractor (if THRD-5 has landed) — a single
   callable protocol, a shipped NoOp default, synchronous invocation from
   MemoryStore, caught-and-logged error handling (verify this last point
   against actual code per repository/hook, don't assume uniformity
   without checking). State why this shape was chosen: it lets a caller
   add LLM-backed behavior without the library ever mandating an LLM
   dependency or network call in its default path.
2. Adapter architecture: the common "thin layer over MemoryStore" shape
   every one of the four adapters (LangChain, OpenAI Agents SDK, MCP,
   Microsoft Agent Framework — adapters/langchain.py, openai_agents.py,
   mcp_server.py, agent_framework.py) follows, and the optional-dependency
   isolation mechanism (each adapter's own extras_require group in
   pyproject.toml, a `_require_*()` import guard so the core package stays
   installable with zero adapter dependencies present).
3. A "how to add a new adapter" or "how to add a new pluggable hook"
   guide, derived from the actual shared pattern identified in sections 1
   and 2 (not invented generically) — concrete enough that a future
   contributor could follow it.

## Acceptance Criteria

- File exists at project-management/design/07-extensibility-architecture.md.
- The error-handling-contract claim in section 1 is verified against
  actual store.py code for each of the listed protocols, not assumed
  uniform — note any protocol whose error handling actually differs.
- Section 2 correctly names all four adapters and their exact extras
  group names, cross-checked against pyproject.toml's
  [project.optional-dependencies] table.
- Zero external product references (naming LangChain/OpenAI Agents SDK/
  MCP/Microsoft Agent Framework as integration targets is fine and
  expected here — this document is specifically about integration
  architecture; do not describe any of them as the inspiration for a
  design choice, only as the thing being integrated with).

Before starting: read DECISIONS.md's ENH-4, PIPE-2, PIPE-3, and STEP-6
entries in full. Before finishing: append a dated entry. In BOARD.html,
set SDD-7's status to "Done" and add a comment. Then
`git add -A && git commit -m "sdd-7: extensibility and integration architecture document"`.
```

---

## SDD-8 — Non-Functional Requirements: Performance, Scalability & Capacity Design Document

```
Before starting: in BOARD.html, set SDD-8's status to "In Progress".

Write project-management/design/08-nfr-performance-capacity.md. Ground it
in db/connection.py (pool sizing), repositories/base.py (EXACT/APPROX
search modes, hybrid RRF cost), PIPE-1's RRF entry, ENH-4's
consolidate_every_n, and BENCHMARKS.md (treat BENCHMARKS.md as the living
data source — this document explains the performance *model*, it does
not re-derive or duplicate BENCHMARKS.md's numbers; link to it instead).

Required sections:
1. Concurrency model: ConnectionPool as the hard concurrency ceiling
   (bounded queue.Queue of DB2_POOL_SIZE connections, default 5, max 20
   per .env.example), what happens under exhaustion (ConnectionPoolExhausted
   after DB2_POOL_TIMEOUT), and sizing guidance (pool size should be >=
   expected concurrent in-flight operations, including any async-wrapped
   calls per THRD-9 if it has landed).
2. Search cost model: EXACT vs APPROX (DiskANN) mode tradeoffs, the
   two-step ID-then-full-row fetch pattern's extra round-trip cost and why
   it exists (a Db2 12.1.5 fp0 constraint, per chunks.py's docstring), the
   added Python-side cost of hybrid=True's keyword scoring + RRF fusion
   (computed over the already-fetched candidate set, no extra SQL).
3. Write cost model: content chunking's effect on write cost for content
   exceeding the configured threshold (multiple embed calls instead of
   one), and consolidate_every_n as the cost-control knob for LLM-backed
   Consolidator/Reconciler hooks (default 1 = every write; document the
   known limitation that the per-scope counter is in-memory, resets on
   restart, and isn't shared across multiple process instances — this is
   already flagged in ENH-4's DECISIONS.md entry, restate it here as a
   capacity-planning caveat, don't silently drop it).
4. Benchmark methodology summary: what BENCHMARKS.md measures (retrieval
   quality, latency/cost, isolation-under-load) and how to reproduce it
   (scripts/run_benchmarks.py), without restating its numeric results —
   link out instead, since those numbers change as new runs are recorded
   and this document should not need to be re-edited every time
   BENCHMARKS.md gets a new run appended.

## Acceptance Criteria

- File exists at project-management/design/08-nfr-performance-capacity.md.
- Contains zero specific benchmark numbers copied from BENCHMARKS.md
  (link to it by relative path instead) — this is a methodology document,
  not a results snapshot, and must not go stale when a new benchmark run
  is appended there.
- The in-memory-counter/no-cross-instance-sharing limitation from ENH-4
  is explicitly restated as a capacity-planning caveat.
- Zero external product references.

Before starting: read DECISIONS.md's ENH-4, PIPE-1, ORC-2, and PH-6
entries in full, and BENCHMARKS.md's current structure (don't copy its
numbers). Before finishing: append a dated entry. In BOARD.html, set
SDD-8's status to "Done" and add a comment. Then
`git add -A && git commit -m "sdd-8: non-functional requirements — performance, scalability, capacity"`.
```

---

## SDD-9 — Deployment, Configuration & Operations Design Document

```
Before starting: in BOARD.html, set SDD-9's status to "In Progress".

Write project-management/design/09-deployment-operations.md. Ground it in
pyproject.toml (packaging/extras), .env.example (full config surface),
.github/workflows/ci.yml and package-check.yml (the real CI pipeline),
db/migrate.py (SchemaPolicy), and scripts/ (six real scripts).

Required sections:
1. Packaging and distribution: what the built wheel actually contains
   ([tool.hatch.build.targets.wheel] packages = only src/agent_memory_sdk
   — project-management/, benchmarks/, scripts/, tests/ are excluded by
   omission), the five extras groups (langchain, openai-agents, mcp,
   agent-framework, all) and what each installs.
2. Configuration reference: every environment variable from .env.example
   as a formal reference table (name, required Y/N, default, purpose) —
   transcribe it completely and accurately, do not summarize or drop any.
3. Schema deployment model: SchemaPolicy.CREATE_IF_NECESSARY (default,
   applies pending migrations) versus REQUIRE_EXISTING (ORC-4, validates
   via SYSCAT catalog queries and refuses to run any DDL) — when an
   operator should choose which, framed as a deployment-environment
   decision (dev/CI likely wants CREATE_IF_NECESSARY, a production
   environment with separate DBA-controlled schema changes likely wants
   REQUIRE_EXISTING).
4. CI/CD pipeline architecture (Mermaid `graph LR` acceptable): the four
   real jobs — lint-typecheck-test (matrix across Python versions),
   integration-test (live Db2 service container), security (pip-audit +
   bandit), and the separate package-check workflow's build/twine-check/
   smoke-test-per-extra job — as a pipeline diagram plus a one-paragraph
   description of what gates a merge.
5. Operational script catalog: one entry per script in scripts/
   (check_connection.py, purge_expired.py, consolidate_pending.py,
   export_memory.py, import_memory.py, run_benchmarks.py) — purpose, when
   an operator should run it, whether it's meant to be cron-scheduled.
6. Error-handling reference for operators: every exception class in
   exceptions.py (StaleWriteError, InvalidMetadataFilterError,
   ScopeMismatchError, ScopeImportError, SchemaPolicyError) with what it
   means operationally and the recommended response, adapted from each
   class's own docstring.

## Acceptance Criteria

- File exists at project-management/design/09-deployment-operations.md.
- The configuration reference table's variable count matches
  .env.example's actual variable count exactly (cross-check).
- The CI pipeline diagram/description names all three ci.yml jobs plus
  the separate package-check.yml workflow correctly (four total gates,
  not folded into three).
- The operational script catalog has exactly six entries, one per real
  file in scripts/ (excluding smoke_test.py if it's a test-only script
  rather than an operator-facing one — verify and state which category
  smoke_test.py falls into rather than guessing).
- All five exceptions from exceptions.py are covered.
- Zero external product references.

Before starting: read DECISIONS.md's Step 1, ORC-4, PH-1, PH-2, PH-4, and
PH-5 entries in full. Before finishing: append a dated entry. In
BOARD.html, set SDD-9's status to "Done" and add a comment. Then
`git add -A && git commit -m "sdd-9: deployment, configuration, and operations design document"`.
```

---

## SDD-10 — Testing & Quality Assurance Strategy Document

```
Before starting: in BOARD.html, set SDD-10's status to "In Progress".

Write project-management/design/10-testing-qa-strategy.md. Ground it in
the real tests/ directory listing, pyproject.toml's [tool.pytest.ini_options]
and coverage config, and INTEGRATION_TESTING.md.

Required sections:
1. Test pyramid: unit tests (tests/*.py, no live Db2 required, the
   majority of the suite), integration tests (tests/integration/, gated
   behind the `integration` pytest marker and auto-skipped without
   DB2_DATABASE set, requiring a real Db2 instance per
   INTEGRATION_TESTING.md), and the benchmarks/ harness (a distinct third
   category — not correctness testing, retrieval-quality/latency/
   isolation-under-load measurement, run on demand not in CI).
2. Coverage policy: the --cov-fail-under=85 gate scoped to
   src/agent_memory_sdk only (tests/*, scripts/* excluded per
   [tool.coverage.run] omit), what's excluded from coverage counting and
   why (pragma: no cover, TYPE_CHECKING blocks, NotImplementedError
   stubs).
3. The scope-isolation test pattern as a named, recurring QA control:
   every repository method with a scoping contract has a parametrized
   cross-scope-isolation test (tests/test_scoping.py, originating from
   VER-5/Step 5) confirming a caller in one scope cannot read/write
   another scope's rows even with a known row id — state this as a
   standing pattern new stories are expected to extend, not a one-time
   test file.
4. Static analysis gates: ruff (lint), mypy --strict (type-check), bandit
   + pip-audit (security, PH-4) — each stated as a merge-blocking CI gate,
   not optional tooling.
5. Test-file-to-story traceability convention: the tests/test_<lowercase-
   story-id>_*.py naming convention already in use (e.g.
   test_pipe5_erasure.py, test_orc2.py) as the mechanism for tracing
   which tests cover which story — describe the convention, do not
   attempt to hand-build a full matrix that will immediately go stale.

## Acceptance Criteria

- File exists at project-management/design/10-testing-qa-strategy.md.
- The test-pyramid section's file/directory references are verified
  against the real tests/ listing, not assumed.
- The coverage-exclusion list matches pyproject.toml's
  [tool.coverage.report] exclude_lines exactly.
- Zero external product references.

Before starting: read DECISIONS.md's Step 5, Step 7, PH-3, and PH-4
entries in full, and INTEGRATION_TESTING.md. Before finishing: append a
dated entry. In BOARD.html, set SDD-10's status to "Done" and add a
comment. Then
`git add -A && git commit -m "sdd-10: testing and quality assurance strategy document"`.
```

---

## SDD-11 — Risk Register & Known Limitations Document

```
Before starting: in BOARD.html, set SDD-11's status to "In Progress".

Write project-management/design/11-risk-register.md: a structured,
internal risk log — not a comparison to any other system, purely this
codebase's own known limitations stated honestly for a review committee
deciding whether to accept them. Format as a table: risk description,
affected component, impact, likelihood, current mitigation (if any),
residual risk / recommended follow-up.

Populate with risks already documented elsewhere in this repo (do not
invent new ones — this story's job is to collect and formalize existing,
already-acknowledged limitations, cross-referencing where each was first
recorded):
1. ENH-4's per-scope consolidation counter is in-memory on a single
   MemoryStore instance — resets on process restart, not shared across
   multiple app instances/processes.
2. Embedding dimension is fixed at construction time (embedding_dim
   parameter); changing it requires a new migration, not a runtime
   reconfiguration.
3. ConnectionPool size is a hard concurrency ceiling (default 5, max 20)
   — sizing must be planned relative to expected concurrent load.
4. PIPE-1's hybrid search keyword scoring is Python-side token-overlap,
   not a database-native text-search feature — a documented, deliberate
   choice, but a real cost/scale ceiling versus a DB-native alternative
   that remains unconfirmed as available (recorded in the PIPE-1 entry).
5. The IngestResolver hook (PIPE-2), when configured, runs one similarity
   search per remember() call, including once per message in a batched
   add_messages() call if THRD-1/THRD-5 have landed — a real per-write
   latency/cost multiplier when both features are enabled together
   (recorded in the EPIC-8 technical-feasibility-check DECISIONS.md
   entry).
6. This SDK depends on the ibm_db native driver and its bundled
   clidriver; environment-specific driver/DLL setup issues are a real
   installation-time failure mode (see db/connection.py's Windows DLL
   guard and Step 1's DECISIONS.md entry).
7. Any other limitation explicitly flagged as "known" or "deliberate
   divergence" anywhere in DECISIONS.md as of the time this story is
   executed (search the file for phrases like "known limitation",
   "deliberately", "does not" to find candidates — do not fabricate
   entries not actually backed by an existing DECISIONS.md statement).

## Acceptance Criteria

- File exists at project-management/design/11-risk-register.md.
- Every risk row cites the DECISIONS.md entry (or code location) it was
  sourced from — no risk is invented without a traceable origin.
- At least the seven risks enumerated above are present, each with all
  six table columns filled in (no blank "mitigation" or "residual risk"
  cells — write "none identified" explicitly rather than leaving it
  empty).
- Zero external product references, and zero risks framed as "worse than
  a competitor" — every risk is stated purely in terms of this system's
  own behavior and consequences.

Before starting: read DECISIONS.md in full (this story specifically
requires having read the whole history, not a targeted subset — its job
is to mine it for limitations). Before finishing: append a dated entry
listing exactly which risks were included and their sources. In
BOARD.html, set SDD-11's status to "Done" and add a comment. Then
`git add -A && git commit -m "sdd-11: risk register and known limitations document"`.
```

---

## SDD-12 — Design Documentation Package Index

```
Before starting: in BOARD.html, set SDD-12's status to "In Progress".
Depends on SDD-1 through SDD-11 — do not start until all eleven are
merged.

Write project-management/design/README.md: the front door for the whole
package. One short paragraph per document (SDD-1 through SDD-11) stating
what it covers and linking to it by relative path, in a sensible reading
order for a first-time reviewer (suggested order: 01 system architecture
-> 02 data architecture -> 03 API/interface spec -> 04 sequence flows ->
05 security -> 06 data governance -> 07 extensibility -> 08 performance/
capacity -> 09 deployment/operations -> 10 testing strategy -> 11 risk
register). Add a short "relationship to other project docs" section
distinguishing this package from ARCHITECTURE.md (living current-state
summary, updated in place), DECISIONS.md (chronological decision log),
and BENCHMARKS.md (living results data) — this package is the
point-in-time, structured design-review artifact; the other three are
not superseded or replaced by it.

## Acceptance Criteria

- File exists at project-management/design/README.md.
- All eleven links resolve to real files that exist in the repo at the
  time this story runs (verify each path, don't assume the filenames
  match this epic's story text exactly if an implementer deviated —
  confirm against what was actually committed).
- The "relationship to other project docs" section is present and
  accurately distinguishes this package from ARCHITECTURE.md/
  DECISIONS.md/BENCHMARKS.md without claiming to replace any of them.
- Zero external product references.

Before starting: read DECISIONS.md in full, including all eleven SDD-*
entries. Before finishing: append a dated entry confirming all eleven
documents exist and are linked. In BOARD.html, set SDD-12's status to
"Done" and add a comment. Then
`git add -A && git commit -m "sdd-12: design documentation package index"`.
```

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

## Working agreement across sessions

Because a build like this often spans multiple agent sessions (or multiple
tools), every step below ends with the same two instructions: read
[`DECISIONS.md`](DECISIONS.md) before starting, and append to it before
finishing. **Do not skip these lines when pasting a step**, even if it feels
redundant within one continuous session — they're what keeps a fresh
session (or a different tool) from silently re-deciding something already
settled, or losing a decision it made that nobody wrote down.

Also commit after each step (`git add -A && git commit -m "step N: ..."`).
That gives you a clean checkpoint to roll back to if a later step goes
sideways, without losing earlier steps.

## MCP tools available in Bob for this project

Bob has several MCP connections configured. Only some fit a headless
Python/Db2 SDK with no UI — use these deliberately, and leave the rest
alone so Bob doesn't burn time setting up things this project doesn't need:

**Use these:**
- **Jira** (ready to use) — tracking for every step. See "Jira tracking"
  below.
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
- **Figma, Carbon, Mural** — design/UI tools; this is a headless library
  with nothing to design. Leave disabled, don't invoke them.
- **Airtable, Amplitude, Monday.com** — require setup, and none fit this
  project (Airtable/Amplitude are structured-data and analytics tools,
  Monday.com would just duplicate Jira as a tracker). Don't set these up
  for this project.

## Jira tracking

Before pasting Step 0, replace every `<JIRA_PROJECT_KEY>` in this file with
your actual Jira project key.

All work is tracked under one Epic, "agent-memory-sdk", with one Story per
build step (Step 1–8), titled "Step N: <step name>" and described using
that step's prompt text. Step 0 creates the Epic and all 8 Stories up
front. From then on, the working agreement is: at the *start* of a step,
transition its Story to In Progress; at the *end*, alongside the
DECISIONS.md append and git commit already required, transition the Story
to Done with a comment summarizing what was built and the commit hash. This
is already folded into each step's prompt below.

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

Using the Jira MCP tool: create an Epic named "agent-memory-sdk" in project
<JIRA_PROJECT_KEY>, then create 8 Stories under it — one per build step
(Step 1 through Step 8) — titled "Step N: <step name>" (use the step names
from PROMPTS.md) with each Story's description set to that step's full
prompt text. Leave all 8 in the backlog/To Do state; later steps will
transition their own Story as work happens.
```

---

## Step 1 — Scaffold

```
Before starting: transition the Jira Story "Step 1: Scaffold" to In
Progress.

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
backend choice and reason, and any other decision/deviation you made. Then
`git add -A && git commit -m "step 1: scaffold"`, and transition "Step 1:
Scaffold" to Done with a comment summarizing what you built and the commit
hash.
```

---

## Step 2 — Schema & migrations

```
Before starting: transition the Jira Story "Step 2: Schema & migrations" to
In Progress.

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
any other deviation. Then `git add -A && git commit -m "step 2: schema"`,
and transition "Step 2: Schema & migrations" to Done with a comment
summarizing what you built and the commit hash.
```

---

## Step 3 — Core models & repositories

```
Before starting: transition the Jira Story "Step 3: Core models &
repositories" to In Progress.

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
parameterized) and any repository/API-shape decisions you made. Then
`git add -A && git commit -m "step 3: models and repositories"`, and
transition "Step 3: Core models & repositories" to Done with a comment
summarizing what you built and the commit hash.
```

---

## Step 4 — Lifecycle: TTL, versioning, forget, consolidation

```
Before starting: transition the Jira Story "Step 4: Lifecycle: TTL,
versioning, forget, consolidation" to In Progress.

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
purge_expired() semantics you settled on. Then
`git add -A && git commit -m "step 4: lifecycle"`, and transition "Step 4:
Lifecycle: TTL, versioning, forget, consolidation" to Done with a comment
summarizing what you built and the commit hash.
```

---

## Step 5 — Governance / scoping enforcement

```
Before starting: transition the Jira Story "Step 5: Governance / scoping
enforcement" to In Progress.

Harden scoping across the SDK: add a `MemoryScope` value object
(tenant_id, agent_id, user_id, thread_id) that's required on every
MemoryStore call instead of loose kwargs. Ensure every generated SQL
statement includes scope predicates (never allow a query with only an id
and no scope check — this is the multi-tenant isolation boundary). Add
tests that assert cross-scope reads return nothing even if you know another
scope's row id.

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry recording the MemoryScope shape and any edge cases you had to
resolve. Then `git add -A && git commit -m "step 5: scoping"`, and
transition "Step 5: Governance / scoping enforcement" to Done with a
comment summarizing what you built and the commit hash.
```

---

## Step 6 — Framework adapters

```
Before starting: transition the Jira Story "Step 6: Framework adapters" to
In Progress.

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
BaseStore maps onto facts vs profiles). Then
`git add -A && git commit -m "step 6: adapters"`, and transition "Step 6:
Framework adapters" to Done with a comment summarizing what you built and
the commit hash.
```

---

## Step 7 — Integration tests

```
Before starting: transition the Jira Story "Step 7: Integration tests" to
In Progress.

Add integration tests that run against a real Db2 LUW instance (document
how to spin one up locally, e.g. the ibmcom/db2 Docker image) gated behind
an env var / pytest marker so they're skippable in CI without Db2. Cover:
schema migration end-to-end, vector search correctness (known nearest
neighbor), scope isolation, TTL purge, forget/tombstone, and each adapter's
basic round-trip (LangChain history, OpenAI Session, MCP tool calls).

Before starting: read DECISIONS.md in full. Before finishing: append a
dated entry noting any gaps found between what DECISIONS.md says and what
the code actually does (fix or flag them). Then
`git add -A && git commit -m "step 7: integration tests"`, and transition
"Step 7: Integration tests" to Done with a comment summarizing what you
built and the commit hash.
```

---

## Step 8 — Docs & examples

```
Before starting: transition the Jira Story "Step 8: Docs & examples" to In
Progress.

Write the README (install, quickstart with docker Db2, the four memory
types explained, scoping model, lifecycle features) and one runnable
example per adapter under examples/. Keep examples short — under 50 lines
each, showing store setup, a remember() call, and a recall() call.

Before starting: read DECISIONS.md in full — the README should reflect it
accurately, not the original Step 0 aspiration if anything changed along
the way. Then `git add -A && git commit -m "step 8: docs and examples"`,
and transition "Step 8: Docs & examples" to Done with a comment
summarizing what you built and the commit hash.
```

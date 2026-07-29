# Design decisions — agent-memory-sdk

This is the single source of truth for decisions made on this project. Every
build step (see `PROMPTS.md`) must read this file before starting and append
a dated entry before finishing. Do not silently deviate from an existing
entry — if a later step needs to change one, add a new entry that
explicitly supersedes it and say why.

## 2026-07-29 — Foundational decisions (made before any code was written)

- **Language:** Python only.
- **Database:** IBM Db2 LUW.
- **Driver:** `ibm_db` (native) + `ibm_db_dbi` (DB-API 2.0 wrapper) as the
  primary connectivity layer. No SQLAlchemy dialect for v1.
- **Memory taxonomy** (synthesized from OpenAI Agents SDK memory docs,
  Oracle AI Agent Memory, and Microsoft Agent Framework/Cosmos DB memory),
  four types:
  1. **working memory** — raw current-session/thread turns, short-lived
  2. **episodic memory** — summarized past runs/threads/events
  3. **semantic memory** — extracted facts + aggregated entity/user profiles
  4. **procedural memory** — learned skills/instructions/how-to knowledge
- **Storage shape:** normalized per-type tables (one table per memory type
  above), not one polymorphic table. Reason: Db2's native vector index only
  activates when the vector column is `NOT NULL`, and each memory type has a
  differently-shaped/dimensioned embedding — a shared column fights that.
  This is closer to the Microsoft/Cosmos approach (separate collections)
  than Oracle's single unified core.
- **Vector search:** Db2 native `VECTOR` column type + `VECTOR_DISTANCE`
  (cosine, euclidean, dot, manhattan supported) + `CREATE VECTOR INDEX`
  (DiskANN-based ANN), with `FETCH EXACT` / `FETCH APPROX` / `FETCH` exposed
  to callers. Introduced in Db2 12.1.2+.
- **Processing model:** extraction/consolidation is pluggable and
  **synchronous by default** (developer-supplied callback run inline on
  `remember()`), with an explicit opt-in extension point to run it async
  later (e.g. via cron). Deliberately does NOT copy Microsoft's mandatory
  background-worker infra — this SDK must work as a plain installable
  library with zero required external services.
- **Framework integration:** framework-agnostic core first; LangChain,
  OpenAI Agents SDK (Session protocol), and MCP adapters are thin optional
  layers built on top, gated behind extras (`pip install
  agent-memory-sdk[langchain]`), not baked into the core.
- **Scoping/governance:** hierarchical scoping on every memory row —
  `tenant_id` (nullable, single-tenant ok) > `agent_id` > `user_id` >
  `thread_id`/`session_id`. Every read/write must be scoped; no cross-scope
  leakage by default. Every repository method requires at least `agent_id`.
- **Lifecycle:** soft-delete/tombstone via `deleted_at` (never hard DELETE
  by default), explicit `forget()` API, per-row `expires_at` TTL with a
  separate `purge_expired()` maintenance method (not automatic), and a
  `version` column for optimistic concurrency.

## 2026-07-30 — Bob MCP tool usage and Jira tracking

- **Decision:** Of Bob's available MCP connections, only three are used for
  this project: **Jira** (issue tracking — one Epic + one Story per build
  step, created in Step 0, transitioned In Progress → Done per step with a
  comment + commit hash), **Product Knowledge** (Milvus-backed semantic
  search over IBM docs — consulted for Db2 VECTOR/index syntax in Step 2 and
  ibm_db driver behavior in Step 1, since these are IBM-specific and
  fast-moving), and **Web search** (Tavily — fallback for anything Product
  Knowledge doesn't cover). Figma, Carbon, and Mural are left unused
  (design/UI tools, no fit for a headless library). Airtable, Amplitude,
  and Monday.com are left unused and unconfigured (require setup, and none
  fit this project's needs — Monday.com would just duplicate Jira).
- **Reason:** Avoid setup/maintenance overhead on tools that don't serve
  this project, while using the two research-relevant tools (Product
  Knowledge, Web search) to reduce the risk of the agent guessing at
  IBM/Db2-specific syntax from possibly-stale trained knowledge.
- **Made during:** Step 0 setup (before any build step ran).
- **Note:** `PROMPTS.md` uses the placeholder `<JIRA_PROJECT_KEY>` — replace
  it with the real Jira project key before pasting Step 0 into Bob.

## Open / not yet decided (fill in as steps happen)

- Embedding dimension(s) per memory type — depends on embedding model
  chosen by the SDK user; document how the schema parameterizes this.
- Build backend for `pyproject.toml` (hatchling vs setuptools) — decide in
  Step 1, record the choice + reason here.
- Exact distance metric per table (cosine vs euclidean vs dot) — decide in
  Step 2, record per-table choice + reason here.
- Whether `content`/`metadata` use CLOB vs VARCHAR vs Db2 JSON type —
  decide in Step 2.

---

### Entry template (copy this for every new decision)

```
## YYYY-MM-DD — <short title>

- **Decision:**
- **Reason:**
- **Made during:** Step N (<step name>)
- **Supersedes:** (link to prior entry, if any — otherwise omit)
```

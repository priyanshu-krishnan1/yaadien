# Architecture — agent-memory-sdk

This is the **current-state** design doc — it describes what the system
looks like *right now*, and gets overwritten (not appended to) as the
build progresses. It complements [`DECISIONS.md`](DECISIONS.md), which is
the chronological log of *why* things are the way they are. When in doubt:
DECISIONS.md answers "why did we do it this way," this file answers "what
does it currently look like."

**Every step must update this file if it changes the design** — not just
DECISIONS.md. As of the last update, no code exists yet; the diagrams below
reflect the Step 0 architecture decisions and will be refined as each step
lands (see the "Last updated" line per section).

---

## 1. System overview

_Last updated: Step 6 — adapters implemented; module paths updated_

```mermaid
flowchart TB
    subgraph Agents["Calling agents"]
        A1[LangChain agent]
        A2[OpenAI Agents SDK agent]
        A3[Any MCP-compatible agent]
    end

    subgraph Adapters["Adapters (optional, thin — pip install agent-memory-sdk[extra])"]
        L["LangChain adapter<br/>Db2ChatMessageHistory<br/>Db2MemoryStore<br/>[langchain]"]
        O["OpenAI Agents SDK adapter<br/>Db2Session<br/>[openai-agents]"]
        M["MCP adapter<br/>remember / recall / forget / list_memories<br/>[mcp]"]
    end

    subgraph Core["agent_memory_sdk core (framework-agnostic)"]
        MS[MemoryStore facade]
        WR[WorkingMemoryRepository]
        ER[EpisodicMemoryRepository]
        SR[SemanticFactRepository]
        PR[EntityProfileRepository]
        PC[ProceduralMemoryRepository]
        CO[Consolidator<br/>pluggable, sync by default]
        EP[EmbeddingProvider<br/>pluggable, caller-supplied]
    end

    subgraph Db2["IBM Db2 LUW"]
        T1[(working_memory)]
        T2[(episodic_memory)]
        T3[(semantic_facts)]
        T4[(entity_profiles)]
        T5[(procedural_memory)]
    end

    A1 --> L --> MS
    A2 --> O --> MS
    A3 --> M --> MS

    MS --> WR --> T1
    MS --> ER --> T2
    MS --> SR --> T3
    MS --> PR --> T4
    MS --> PC --> T5

    WR -. raw memories .-> CO
    ER -. raw memories .-> CO
    CO -. derived facts/profiles/skills .-> SR
    CO -. derived facts/profiles/skills .-> PR
    CO -. derived facts/profiles/skills .-> PC

    EP -. text to vector .-> WR
    EP -. text to vector .-> ER
    EP -. text to vector .-> SR
    EP -. text to vector .-> PR
    EP -. text to vector .-> PC
```

Key points this diagram encodes (see DECISIONS.md for the reasoning):
- Adapters are optional and sit *outside* the core — core has zero
  framework dependencies.  Each adapter is gated behind its own extras
  group in `pyproject.toml`.
- Every repository talks to its own Db2 table (normalized, not one
  polymorphic table).
- Consolidation is a pluggable callback the core *can* invoke inline; it's
  not a separate always-on service.
- Embeddings are supplied by the caller via `EmbeddingProvider`; the SDK
  doesn't ship a specific embedding model.

Actual module paths (as of Step 6):
- `src/agent_memory_sdk/types.py` — `EmbeddingProvider` (Protocol),
  `Consolidator` (Protocol), `NoOpConsolidator` (default no-op),
  `DistanceMetric` (enum), `SearchMode` (enum)
- `src/agent_memory_sdk/exceptions.py` — `StaleWriteError`
- `src/agent_memory_sdk/models.py` — `MemoryScope`, `WorkingMemory`,
  `EpisodicMemory`, `SemanticFact`, `EntityProfile`, `ProceduralMemory`
- `src/agent_memory_sdk/repositories/base.py` — `BaseRepository` ABC
  (includes `forget`, `update`, `purge_expired` since Step 4)
- `src/agent_memory_sdk/repositories/working.py` — `WorkingMemoryRepository`
- `src/agent_memory_sdk/repositories/episodic.py` — `EpisodicMemoryRepository`
- `src/agent_memory_sdk/repositories/facts.py` — `SemanticFactRepository`
- `src/agent_memory_sdk/repositories/profiles.py` — `EntityProfileRepository`
- `src/agent_memory_sdk/repositories/procedural.py` — `ProceduralMemoryRepository`
- `src/agent_memory_sdk/store.py` — `MemoryStore` facade
  (includes `remember`, `forget`, `purge_expired` since Step 4)
- `src/agent_memory_sdk/adapters/__init__.py` — adapter package (docstring only)
- `src/agent_memory_sdk/adapters/langchain.py` — `Db2ChatMessageHistory`
  (LangChain `BaseChatMessageHistory` backed by `store.working`) +
  `Db2MemoryStore` (LangChain `BaseStore[str, str]` backed by
  `store.facts` or `store.profiles`)
- `src/agent_memory_sdk/adapters/openai_agents.py` — `Db2Session`
  (OpenAI Agents SDK `Session` protocol backed by `store.working`;
  bonus `recall_episodes()` via `store.episodic`)
- `src/agent_memory_sdk/adapters/mcp_server.py` — `create_server(store)`
  factory returning an MCP `Server` with four tools: `remember`,
  `recall`, `forget`, `list_memories`
- `scripts/purge_expired.py` — cron-callable maintenance script
- `scripts/consolidate_pending.py` — reference async consolidation pattern

---

## 2. Scoping model

_Last updated: Step 0 (design only, no code yet) — MemoryScope model built in Step 3_

```mermaid
flowchart LR
    T[tenant_id<br/>nullable] --> AG[agent_id]
    AG --> U[user_id]
    U --> TH[thread_id / session_id]
```

Every row in every memory table carries all four columns. Every
`MemoryStore` call requires at least `agent_id`; queries always filter by
scope before ranking by vector distance. See `MemoryScope` (built in Step
5).

---

## 3. Schema (entity-relationship)

_Last updated: Step 7 — `expires_at` indexes changed from partial to plain (Db2 12.1.5 fp0 does not support filtered indexes)_

Column type legend:
- `id` → `VARCHAR(36)` (UUID)
- `*_id` scope cols → `VARCHAR(128)`, tenant_id nullable, agent_id NOT NULL
- `content` → `CLOB(65536)` (64 KB; see DECISIONS.md Step 2 entry)
- `metadata` → `VARCHAR(4096)` (JSON text; see DECISIONS.md Step 2 entry)
- `embedding` → `VECTOR(1536, FLOAT32) NOT NULL` (no DB-side default; application layer always supplies an explicit vector — real embedding or zero-vector sentinel — on every INSERT)
- `created_at`, `updated_at` → `TIMESTAMP NOT NULL DEFAULT CURRENT TIMESTAMP`
- `expires_at`, `deleted_at` → `TIMESTAMP` (nullable)
- `version` → `INTEGER NOT NULL DEFAULT 1`

Each table has: a `CREATE VECTOR INDEX … WITH DISTANCE COSINE`, a composite
scope index on `(agent_id, tenant_id, user_id, thread_id)`, an agent-only
index, and a plain (unfiltered) index on `expires_at`.  The `WHERE expires_at
IS NOT NULL` predicate was removed from all five `ix_*_expires` indexes in
migration `0002` because Db2 12.1.5 fp0 does not support partial (filtered)
indexes (`SQL0104N`).  Rows with NULL `expires_at` incur negligible extra
index overhead.

Migration runner: `src/agent_memory_sdk/db/migrate.py` (Migrator class).
Migration files: `src/agent_memory_sdk/db/migrations/000N_*.sql`.

```mermaid
erDiagram
    schema_migrations {
        VARCHAR_255 version PK
        TIMESTAMP applied_at
    }

    working_memory {
        VARCHAR_36 id PK
        VARCHAR_128 tenant_id "nullable"
        VARCHAR_128 agent_id "NOT NULL"
        VARCHAR_128 user_id "nullable"
        VARCHAR_128 thread_id "nullable"
        CLOB_65536 content "NOT NULL"
        VARCHAR_4096 metadata "NOT NULL default {}"
        VECTOR_1536_FLOAT32 embedding "NOT NULL, app-supplied"
        TIMESTAMP created_at "NOT NULL"
        TIMESTAMP updated_at "NOT NULL"
        TIMESTAMP expires_at "nullable"
        INTEGER version "NOT NULL default 1"
        TIMESTAMP deleted_at "nullable"
    }

    episodic_memory {
        VARCHAR_36 id PK
        VARCHAR_128 tenant_id "nullable"
        VARCHAR_128 agent_id "NOT NULL"
        VARCHAR_128 user_id "nullable"
        VARCHAR_128 thread_id "nullable"
        CLOB_65536 content "NOT NULL"
        VARCHAR_4096 metadata "NOT NULL default {}"
        VECTOR_1536_FLOAT32 embedding "NOT NULL, app-supplied"
        TIMESTAMP created_at "NOT NULL"
        TIMESTAMP updated_at "NOT NULL"
        TIMESTAMP expires_at "nullable"
        INTEGER version "NOT NULL default 1"
        TIMESTAMP deleted_at "nullable"
    }

    semantic_facts {
        VARCHAR_36 id PK
        VARCHAR_128 tenant_id "nullable"
        VARCHAR_128 agent_id "NOT NULL"
        VARCHAR_128 user_id "nullable"
        VARCHAR_128 thread_id "nullable"
        CLOB_65536 content "NOT NULL"
        VARCHAR_4096 metadata "NOT NULL default {}"
        VECTOR_1536_FLOAT32 embedding "NOT NULL, app-supplied"
        TIMESTAMP created_at "NOT NULL"
        TIMESTAMP updated_at "NOT NULL"
        TIMESTAMP expires_at "nullable"
        INTEGER version "NOT NULL default 1"
        TIMESTAMP deleted_at "nullable"
    }

    entity_profiles {
        VARCHAR_36 id PK
        VARCHAR_128 tenant_id "nullable"
        VARCHAR_128 agent_id "NOT NULL"
        VARCHAR_128 user_id "nullable"
        VARCHAR_128 thread_id "nullable"
        CLOB_65536 content "NOT NULL"
        VARCHAR_4096 metadata "NOT NULL default {}"
        VECTOR_1536_FLOAT32 embedding "NOT NULL, app-supplied"
        TIMESTAMP created_at "NOT NULL"
        TIMESTAMP updated_at "NOT NULL"
        TIMESTAMP expires_at "nullable"
        INTEGER version "NOT NULL default 1"
        TIMESTAMP deleted_at "nullable"
    }

    procedural_memory {
        VARCHAR_36 id PK
        VARCHAR_128 tenant_id "nullable"
        VARCHAR_128 agent_id "NOT NULL"
        VARCHAR_128 user_id "nullable"
        VARCHAR_128 thread_id "nullable"
        CLOB_65536 content "NOT NULL"
        VARCHAR_4096 metadata "NOT NULL default {}"
        VECTOR_1536_FLOAT32 embedding "NOT NULL, app-supplied"
        TIMESTAMP created_at "NOT NULL"
        TIMESTAMP updated_at "NOT NULL"
        TIMESTAMP expires_at "nullable"
        INTEGER version "NOT NULL default 1"
        TIMESTAMP deleted_at "nullable"
    }
```

---

## 4. Flow: `remember()`

_Last updated: Step 4 — sync consolidation implemented; async pattern documented_

```mermaid
sequenceDiagram
    participant Agent
    participant MemoryStore
    participant Repo as Type Repository
    participant Consolidator
    participant Db2

    Agent->>MemoryStore: remember(record, scope)
    MemoryStore->>Repo: create(record, scope)
    Repo->>Db2: INSERT ... (VECTOR NOT NULL, version=1)
    Db2-->>Repo: stored record (id, created_at set)
    Repo-->>MemoryStore: stored record

    alt memory_type is working or episodic
        MemoryStore->>Consolidator: __call__([stored record])
        note over Consolidator: NoOpConsolidator (default): returns []
        note over Consolidator: LLMConsolidator (custom): returns derived records
        Consolidator-->>MemoryStore: [] or [SemanticFact, EntityProfile, ProceduralMemory, ...]
        loop for each derived record
            MemoryStore->>Repo: create(derived_record, scope)
            Repo->>Db2: INSERT derived memory
        end
        note over MemoryStore: Consolidator errors caught + logged
        note over MemoryStore: never propagated to caller
    end

    MemoryStore-->>Agent: stored record
```

**Async / out-of-band extension point (not yet implemented as schema):**
When consolidation is too slow for the inline path, omit the consolidator,
mark rows with `metadata={"consolidated": false}` at write time, and run
`scripts/consolidate_pending.py` as a cron job.  See
`src/agent_memory_sdk/types.py` (Consolidator docstring) and DECISIONS.md
Step 4 entry for details.

## 5. Flow: `recall()` / semantic search

_Last updated: Step 7 — two-step query shape documented (Db2 12.1.5 fp0 cannot combine VECTOR_SERIALIZE in SELECT with VECTOR_DISTANCE in ORDER BY)_

```mermaid
sequenceDiagram
    participant Agent
    participant MemoryStore
    participant Repo as Type Repository
    participant Db2

    Agent->>MemoryStore: recall(query, scope, top_k, mode)
    MemoryStore->>Repo: search(query_embedding, scope, top_k, mode)

    note over Repo: Step 1 — rank by distance, return IDs only<br/>(no VECTOR_SERIALIZE in SELECT list)
    Repo->>Db2: SELECT id FROM &lt;table&gt;<br/>WHERE &lt;scope&gt; AND deleted_at IS NULL<br/>ORDER BY VECTOR_DISTANCE(embedding, CAST('…' AS VECTOR), COSINE)<br/>FETCH FIRST top_k ROWS ONLY [APPROX]
    Db2-->>Repo: ordered_ids (nearest-first)

    note over Repo: Step 2 — fetch full rows by ID<br/>(uses VECTOR_SERIALIZE in SELECT)
    Repo->>Db2: SELECT id, …, VECTOR_SERIALIZE(embedding) AS embedding, …<br/>FROM &lt;table&gt; WHERE id IN (id1, id2, …) AND deleted_at IS NULL
    Db2-->>Repo: unordered full rows

    note over Repo: Reorder rows in Python to restore<br/>nearest-first ordering from step 1
    Repo-->>MemoryStore: typed memory objects (nearest-first)
    MemoryStore-->>Agent: ranked memories
```

---

## Where design docs live vs. Bob's MCP tools

We evaluated Bob's connected MCP tools (Jira, Airtable, Amplitude, Carbon,
Figma, Monday.com, Mural, Product Knowledge, Web search) for storing
architecture/flow design and none fit: Mural's MCP access is read-only,
Product Knowledge is a read-only search index over IBM's own docs, Jira is
a tracker not a design-doc tool, Figma/Carbon are UI-only, and
Airtable/Amplitude/Monday.com don't fit this project (see DECISIONS.md
2026-07-30 entry). This file — versioned with the code, diffable, no
external auth dependency — is the actual answer. Jira Stories may *link* to
this file, but should not duplicate its content.

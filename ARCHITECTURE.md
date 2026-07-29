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

_Last updated: Step 0 (design only, no code yet)_

```mermaid
flowchart TB
    subgraph Agents["Calling agents"]
        A1[LangChain agent]
        A2[OpenAI Agents SDK agent]
        A3[Any MCP-compatible agent]
    end

    subgraph Adapters["Adapters (optional, thin)"]
        L[LangChain adapter<br/>BaseChatMessageHistory / BaseStore]
        O[OpenAI Agents SDK adapter<br/>Session protocol]
        M[MCP adapter<br/>remember/recall/forget/list tools]
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
  framework dependencies.
- Every repository talks to its own Db2 table (normalized, not one
  polymorphic table).
- Consolidation is a pluggable callback the core *can* invoke inline; it's
  not a separate always-on service.
- Embeddings are supplied by the caller via `EmbeddingProvider`; the SDK
  doesn't ship a specific embedding model.

---

## 2. Scoping model

_Last updated: Step 0 (design only, no code yet)_

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

_Last updated: Step 0 (design only — exact column types/metrics chosen in
Step 2, this diagram should be revised to match)_

```mermaid
erDiagram
    working_memory {
        id id PK
        tenant_id string
        agent_id string
        user_id string
        thread_id string
        content text
        metadata json
        embedding vector "NOT NULL"
        created_at timestamp
        expires_at timestamp
        version int
        deleted_at timestamp
    }
    episodic_memory {
        id id PK
        tenant_id string
        agent_id string
        user_id string
        thread_id string
        content text
        metadata json
        embedding vector "NOT NULL"
        created_at timestamp
        expires_at timestamp
        version int
        deleted_at timestamp
    }
    semantic_facts {
        id id PK
        tenant_id string
        agent_id string
        user_id string
        content text
        metadata json
        embedding vector "NOT NULL"
        version int
        deleted_at timestamp
    }
    entity_profiles {
        id id PK
        tenant_id string
        agent_id string
        user_id string
        content text
        metadata json
        embedding vector "NOT NULL"
        version int
        deleted_at timestamp
    }
    procedural_memory {
        id id PK
        tenant_id string
        agent_id string
        content text
        metadata json
        embedding vector "NOT NULL"
        version int
        deleted_at timestamp
    }
```

---

## 4. Flow: `remember()`

_Last updated: Step 0 (design only, no code yet)_

```mermaid
sequenceDiagram
    participant Agent
    participant MemoryStore
    participant Repo as Type Repository
    participant Consolidator
    participant Db2

    Agent->>MemoryStore: remember(content, type, scope)
    MemoryStore->>Repo: create(content, scope, embedding)
    Repo->>Db2: INSERT ... (VECTOR NOT NULL)
    Db2-->>Repo: row id
    MemoryStore->>Consolidator: on_write(raw memory)  [if configured]
    Consolidator-->>MemoryStore: derived facts/profile/skill updates
    MemoryStore->>Repo: upsert derived memory (facts/profiles/procedural)
    Repo->>Db2: INSERT/UPDATE
    MemoryStore-->>Agent: memory id
```

## 5. Flow: `recall()` / semantic search

_Last updated: Step 0 (design only, no code yet)_

```mermaid
sequenceDiagram
    participant Agent
    participant MemoryStore
    participant Repo as Type Repository
    participant Db2

    Agent->>MemoryStore: recall(query, scope, top_k, mode)
    MemoryStore->>Repo: search(query_embedding, scope, top_k, mode)
    Repo->>Db2: SELECT ... WHERE scope predicates<br/>ORDER BY VECTOR_DISTANCE(...)<br/>FETCH EXACT|APPROX
    Db2-->>Repo: ranked rows
    Repo-->>MemoryStore: typed memory objects
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

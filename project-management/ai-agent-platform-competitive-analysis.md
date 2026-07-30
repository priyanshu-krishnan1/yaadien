# The AI Agent Memory Ecosystem: A Technical Architecture and Market Study (July 2026)

## TL;DR
- Agent memory has moved from research curiosity to a production requirement, and the market has bifurcated into three tiers: **memory-layer SDKs** (Mem0, Zep, LangMem, Cognee) that bolt onto any framework; **agent-runtime memory** (Letta/MemGPT, LangGraph, CrewAI, Agno) where memory is native to the execution engine; and **converged-database memory** (Oracle AI Agent Memory, Redis Iris, Couchbase, Weaviate Engram, Neo4j) where memory is a governed feature of an enterprise data platform.
- A remarkably consistent cognitive taxonomy has converged across the field — working/short-term, semantic, episodic, and procedural memory — while the true competitive battleground has shifted to governance, multi-tenant isolation, temporal/bi-temporal fact modeling, retrieval quality (hybrid search + reranking), and cost control (bounded per-turn tokens).
- No vendor has satisfactorily solved memory federation across clouds, portability/standard APIs, explainability, benchmarking/quality scoring, or trust/privacy governance; these are the open gaps most likely to define 2027-2030.

## Key Findings

1. **The cognitive taxonomy is now standard.** Oracle, MIRIX, LangMem, Letta, CrewAI, and Mem0 all converge on variations of working (short-term), semantic (facts), episodic (past events), and procedural (learned behaviors) memory, mirroring decades of cognitive-science work. This taxonomy is described almost identically across independent sources.

2. **Two philosophies dominate: passive extraction vs. agentic self-editing.** Mem0/LangMem/Bedrock use asynchronous LLM extraction pipelines (predictable, token-efficient, consistent). Letta/MemGPT use the agent's own reasoning to edit memory blocks (adaptive but higher token cost and quality-dependent on the model). Both go beyond classic RAG.

3. **Hyperscalers shipped managed memory in 2025-2026.** AWS Bedrock AgentCore Memory, Google Vertex AI Memory Bank, Microsoft Agent Framework (GA April 3, 2026), and Oracle AI Agent Memory all launched managed memory services with short-term/long-term tiers and asynchronous extraction.

4. **Converged databases are the enterprise play.** Oracle (AI Database), Redis (Iris/Agent Memory), Couchbase (AI Data Plane), MongoDB (Atlas + LangGraph), and Neo4j position memory as a governed feature of existing data infrastructure, emphasizing "don't build a second, less-governed copy of your business."

5. **Temporal knowledge graphs are the differentiator for accuracy.** Zep/Graphiti's bi-temporal model (valid-time + ingestion-time, fact invalidation not deletion) leads on enterprise benchmarks (LongMemEval) and provides auditable provenance that pure vector stores lack.

6. **Academic work is racing ahead of products** on multi-agent memory (MIRIX), memory operating systems (MemoryOS), and efficiency (LightMem's 10-38× token reduction), while benchmarks (LongMemEval, LOCOMO) have become the industry's shared yardstick. LongMemEval (Wu et al., arXiv 2410.10813, ICLR 2025) reports that long-context LLMs drop 30%–60% and commercial systems such as GPT-4o achieve only 30%–70% accuracy in memorizing information across sustained interactions — the "~30% accuracy drop" figure vendors now cite.

## Details: Platform-by-Platform Analysis

### Open-Source Memory Layers & Frameworks

**Mem0** (github.com/mem0ai/mem0; docs.mem0.ai; arXiv 2504.19413). A universal, self-improving memory layer that sits alongside any LLM/agent framework. Architecture: a three-stage pipeline (extract → consolidate/update → retrieve) with a graph variant (Mem0ᵍ) for relational reasoning. Memory layers: conversation, session, user, organizational; maps onto working/factual/episodic/semantic/graph types. Storage is hybrid — vectors + graph + key-value; default LLM gpt-5-mini, default embeddings text-embedding-3-small. Memory reconciliation uses LLM-chosen ADD/UPDATE/DELETE/NONE actions with configurable fact-extraction and update prompts. On LOCOMO the paper reports state-of-the-art with large latency/token savings; the repo reports a V3 algorithm (April 2026) scoring 91.6 on LOCOMO via single-pass extraction. Enterprise: SOC 2 Type 1 and HIPAA compliant, BYOK, zero-trust, Kubernetes/private-cloud/air-gapped deployment, audit logging. ~48,000 GitHub stars; closed a **$24M Series A in October 2025** (backed by Y Combinator and Peak XV, per PR Newswire/Morningstar and 2026 industry reviews). Known limitations: staleness/conflicting memories weakly managed by recency/LRU; error propagation from LLM extraction; no temporal validity window; multi-agent shared memory requires custom work.

**Letta (formerly MemGPT)** (docs.letta.com; MemGPT arXiv 2310.08560). An agent *runtime*, not just a memory layer. Architecture: stateful memory server + memory blocks, with an OS-inspired two-tier model — in-context "core memory" (persistent labeled blocks like persona/human, always injected, size-limited, individually persisted in a DB with a unique block_id, editable via tools) and out-of-context "archival/recall memory" (external DB retrieved via search). Backed by PostgreSQL + pgvector; supports vector and graph DBs. Distinctive features: agent self-editing via memory tools (memory_insert/replace/rethink/finish_edits), recursive summarization on context overflow, multi-agent shared memory blocks, and "sleep-time agents" that refine memory asynchronously (non-blocking). Letta's archival memory is agent-scoped and exposed via REST endpoints (POST/GET/DELETE /v1/agents/{id}/archival-memory). Weakness: LLM-managed paging adds latency and token cost per interaction; requires persistent infrastructure (PostgreSQL/Docker).

**LangGraph Memory / LangMem** (docs.langchain.com; langchain.com/blog/langmem-sdk-launch). LangGraph splits memory into short-term (thread-scoped state persisted via checkpointers — MemorySaver, SqliteSaver, PostgresSaver, Redis) and long-term (cross-thread via the BaseStore, JSON documents organized by namespaces and keys, with optional vector index). LangMem is LangChain's dedicated SDK layering semantic/episodic/procedural memory with an LLM-driven Memory Manager (hot-path tools + background consolidation), pluggable storage, namespacing for isolation, and prompt-optimization for procedural memory. LangMem consolidation merges related memories and resolves contradictions to avoid "memory hoarding." Adoption: ~746K monthly PyPI downloads, but still pre-1.0 (latest 0.0.30, October 2025). Note: legacy ConversationBufferMemory was deprecated in v0.3.1.

**Cognee** (Apache 2.0, ~28K GitHub stars). An extract-cognify-load (ECL) pipeline combining graph + vector + session memory; can run on a single self-hosted Postgres, and supports external graph DBs (Kuzu) and Redis (for fast session/working memory staging with distributed locks for multi-instance coordination). Best for local-first, privacy-critical, graph-reasoning deployments.

**CrewAI Memory** (docs.crewai.com). Historically four types: short-term (ChromaDB/LanceDB + RAG), long-term (SQLite, tracks task success/failure across runs), entity (RAG-based knowledge about people/companies/concepts), and contextual (orchestration layer combining the others); enabled via memory=True. CrewAI v1.15+ introduced a *unified* Memory class — a single LLM-driven API with hierarchical filesystem-like scopes and composite scoring (semantic similarity + recency + importance), plus built-in Flow memory (remember/recall/extract_memories). Known issues: default RAG gives equal weight to trivial and important context; concurrency ("database is locked") issues under parallel crews; config confusion across memory_config/external_memory/embedder.

**Agno Memory** (docs.agno.com). Three memory systems: session storage (chat history/state), user memories (persistent facts, user_id-scoped for tenant isolation), and session summaries. Agno v2.0 unified sessions, memories, evals, and metrics into a single database (SqliteDb/PostgresDb/MongoDb). Flags: enable_user_memories, enable_agentic_memory, enable_session_summaries, add_history_to_context. Known bug reports: user memories bleeding into session storage; team user-memory timing issues.

**AutoGen Memory** — message-list-based with external integrations (including a documented Mem0 integration); Microsoft placed AutoGen (and Semantic Kernel) into maintenance mode in October 2025, consolidating into Microsoft Agent Framework.

**Semantic Kernel Memory** — orchestration SDK (Python/.NET/Java) with Vector Store abstractions (Azure AI Search, Qdrant, Chroma, Pinecone, Redis) and a separate Kernel Memory microservice for ingestion (OCR, chunking, embedding, indexing). Now in maintenance mode; the older ISemanticTextMemory replaced by Vector Store abstractions; Microsoft supports SK for at least one year after Agent Framework GA.

**Others (verified capability):** LlamaIndex offers memory blocks and chat stores; Haystack, Camel AI, OpenManus, Continue.dev, and AnythingLLM offer conversation/retrieval memory but lack dedicated governed long-term memory SDKs comparable to the above — treat these as framework-embedded memory rather than standalone memory platforms. (Detailed per-feature verification for these five could not be completed within source limits — flagged as a gap.)

### Cloud Vendor Memory Services

**Amazon Bedrock AgentCore Memory** (docs.aws.amazon.com/bedrock-agentcore) — in preview. Fully-managed. Short-term memory: raw events via CreateEvent/GetEvent/ListEvents/DeleteEvent, scoped by actorId + sessionId, retention up to 365 days (eventExpiryDuration set per-event at write time, not extendable retroactively). Long-term memory: asynchronous extraction with configurable memory strategies (summarization, semantic, user preferences) — built-in or custom (custom system prompt + model choice), organized by namespaces. Pipeline: extraction → consolidation. Security: customer-managed KMS keys. Retrieval via RetrieveMemoryRecords/ListMemoryRecords with semantic search.

**Google Vertex AI Agent Engine Memory Bank** (cloud.google.com/vertex-ai) — public preview, now with a Cloud Console UI. Managed long-term memory: LLM (Gemini)-based asynchronous extraction from Sessions (CreateSession/AppendEvent/ListEvents), memories scoped by user_id, similarity-search retrieval, and contradiction resolution over time. Integrated with ADK (VertexAiMemoryBankService via --memory_service_uri agentengine://) and third-party frameworks (LangGraph, CrewAI). VertexAiRagMemoryService stores conversations in Knowledge Engine with vector retrieval. Express-mode free tier via Gmail signup.

**Microsoft Agent Framework** (learn.microsoft.com/agent-framework; devblogs.microsoft.com) — the unification of AutoGen + Semantic Kernel; RC February 2026, **1.0 GA April 3, 2026**, MIT-licensed. "Agent Memory and Context Providers" — a pluggable memory architecture supporting conversational history, persistent key-value state, and vector-based retrieval; core packages include agent-framework-mem0. Foundry hosted agents bill on consumption with scale-to-zero (billing began April 22, 2026). Azure Managed Redis co-developed with Microsoft as native memory layer. Process Framework GA targeted Q2 2026.

**Oracle AI Agent Memory** (oracle.com/database/ai-agent-memory; PyPI oracleagentmemory; docs.oracle.com/en/database/oracle/agent-memory/26.4). Model- and framework-agnostic Python SDK (v26.4) on Oracle AI Database (23ai/26ai). Architecture: unified client over the converged database (vector + relational + graph + JSON in one engine). Supports all four memory types (working/semantic/episodic/procedural) as "four access patterns over the same underlying state." Features: short-term threads with summaries and "context cards," long-term durable memories with vector search, automatic LLM-based extraction, hybrid retrieval (semantic + keyword + metadata filters + record types + explicit scope), prompt-time compaction, and per-record user/agent/thread/timestamp scoping for audit and erasure (GDPR-style). Multi-tenant isolation enforced at store layer. Framework integrations: LangGraph, Claude Agent SDK, OpenAI Agents SDK, WayFlow, custom harnesses. Benchmark: reports 93.8% on LongMemEval (469/500) with gpt-5.5 + nomic-embed-v1.5 + HNSW; ~9.5× lower per-request tokens vs flat-history at 80 turns; won 48 turns to flat-history's 13 (judged by gpt-5.4). GA expected CY2026.

**OpenAI Agents SDK** (openai.github.io/openai-agents-python) — Sessions provide conversation history via a SessionStore interface (SQLite/Redis/RDB/Conversations API). The Conversations API offers server-managed persistence with no TTL; Responses API chains via previous_response_id (GA). Sandbox agents (beta) add a separate file-based "memory" that distills lessons from prior runs into MEMORY.md/memory_summary.md, with live_update, treating memory as guidance to reduce agent/user/context cost.

**Anthropic Claude Agent SDK / Memory Tool** (open source, github.com/anthropics) — Memory Tool (beta, launched Sept 29, 2025) exposes a client-side /memories filesystem with view/create/str_replace/insert/delete/rename commands; developers implement storage. Requires beta header context-management-2025-06-27. Combined with Context Editing, Anthropic reports ~84% token reduction on long-running agents. Managed Agents add persistent, versioned, filesystem-mounted memory (~$0.08/session-hour on top of tokens) — but Claude-only (lock-in). SDK Sessions track within-session state; cross-session persistence is developer-owned.

**NVIDIA NeMo Agent Toolkit** (formerly AgentIQ/AIQ; package nvidia-nat; docs.nvidia.com/nemo/agent-toolkit) — a framework-agnostic Memory subsystem that stores/retrieves conversation history, preferences, and long-term memory via pluggable providers: Mem0 (nvidia-nat-mem0ai), Redis (nvidia-nat-redis), Zep (nvidia-nat-zep-cloud). Not a proprietary memory store — an orchestration/interface layer. Roadmap: "improved memory interface to support self-improving agents."

**Databricks** — "AI agent memory" backed by Lakebase (fully-managed Postgres OLTP, scale-to-zero, branching) within Mosaic AI Agent Framework/Agent Bricks (docs.databricks.com/…/stateful-agents). Short-term (thread IDs + LangGraph checkpointing) and long-term (auto-extract across sessions); vector retrieval via Mosaic AI Vector Search; Delta + Unity Catalog for governance. Approach is a managed *store* + official templates (agent-langgraph-advanced, agent-openai-advanced) rather than a fully abstracted memory API.

### Enterprise/Infra Vendors

**Neo4j** — has BOTH a first-party Neo4j Labs SDK (neo4j-labs/agent-memory; Python + TypeScript; hosted NAMS service; MCP server with 16 tools; v0.4.0 May 2026) with an explicit three-tier model (short-term conversations, long-term knowledge-graph entities/preferences with entity resolution/dedup, and reasoning memory) AND the popular Graphiti integration. Caveat: the Labs SDK is marked "Experimental / Community Supported," not officially GA-backed.

**Zep / Graphiti** (arXiv 2501.13956; github.com/getzep/graphiti) — a temporal knowledge graph memory layer. Graphiti builds bi-temporal context graphs (valid-time + ingestion-time), invalidating rather than deleting superseded facts; three subgraphs (episodic raw data, semantic entities/facts, community summaries). Retrieval composes cosine similarity + BM25 full-text + breadth-first graph traversal with reranking (RRF, MMR, cross-encoder). Reports 94.8% vs MemGPT's 93.4% on DMR and up to 18.5% gains on LongMemEval with 90% lower latency. Runs on Neo4j/FalkorDB self-hosted or Zep's Context Graph Engine (sub-200ms p95). Graphiti is Apache-2.0 with **~27.3K GitHub stars** (latest release v0.29.1, May 21), and ships a Graphiti MCP Server 1.0. Note: Zep retired its self-hosted Community Edition in 2025; Graphiti itself remains open source.

**Redis** — Redis Agent Memory Server (open source, redis.github.io/agent-memory-server) and managed **Redis Iris / Agent Memory**, launched the week of **May 18, 2026** as part of the Redis "Context Engine" (three components: Redis Context Retriever, Redis Agent Memory, Redis Data Integration; per SiliconANGLE). Two-tier: working/session memory (sub-millisecond, TTL-controlled) + long-term memory (text + vector embeddings, semantic retrieval) with automatic background extraction/promotion, deduplication, summarization, consolidation. REST + MCP interfaces; 100+ LLM providers via LiteLLM; backends Redis/Pinecone/Chroma/PostgreSQL. Redis was ranked the #1 AI-agent data-storage tool in the **2025 Stack Overflow Developer Survey (43% of AI-agent developers)**, tying the GitHub MCP server; Redis reports surpassing $300M ARR (Techzine, May 2026). CEO Rowan Trollope framed Iris as "just a flip to let the agent pull the data instead of presupposing and stuffing it into the pipeline." Positioned as low-latency memory substrate; extraction logic optional.

**MongoDB** — MongoDB Store for LangGraph (long-term, cross-thread, JSON docs under namespaces, Atlas Vector Search + Voyage AI embeddings, TTL for decay) + MongoDB Checkpointer (short-term state). Delivered via LangGraph integration (GA), not a standalone branded memory service.

**Couchbase** — Couchbase Agent Memory, part of the AI Data Plane (GA July 2026; docs.couchbase.com/ai/build/agent-memory): unified persistent memory across user sessions + real-time context retrieval + enterprise-managed MCP server, spanning Capella (managed), self-managed, and edge/mobile.

**Weaviate** — Engram, a managed memory & context service (GA June 15, 2026): asynchronous extract-transform-commit pipeline with fact extraction, deduplication, reconciliation ("purposeful forgetting"), user-scoped topics/memory groups, over vector/BM25/hybrid search with multi-tenancy isolation. A true agent-memory layer beyond generic vector search.

**ServiceNow** — built-in AI Agent memory (sn_aia module): short-term (single conversation, inter-agent data sharing) + long-term (persists across sessions, user-scoped, stored in the sn_aia_memory table in vector format), enabled via properties (sn_aia.ltm.enable_long_term_memory). A platform-native memory feature.

**Snowflake Cortex Agents** — stateful "Threads" (server-side context across turns) but no dedicated long-term memory service; cross-session persistence is developer-built (store thread_id/parent_message_id). Retrieval via Cortex Search (vector) + Cortex Analyst (SQL). Building-block.

**SAP Joule** — multi-turn context via context/task IDs and an A2A-based orchestration; persistent memory is a documented HANA Cloud pattern (SAP Community) rather than a packaged memory service. Joule Studio agent builder GA Q1 2026. Building-block + patterns.

**IBM** — no dedicated agent-memory SDK. watsonx Orchestrate (next-gen announced Think 2026, May 5, 2026, private preview) is positioned as an agentic *control plane* for governing/auditing fleets of agents (A2A support, guardrails), with memory/state persistence handled by Db2/watsonx.data and Redis (its dev-edition uses a wxo-server-redis container). IBM's strengths are governance and heterogeneous orchestration, not a memory substrate.

**Qdrant, Elastic, SingleStore, Pinecone** — primarily storage primitives. Pinecone offers namespaces + metadata filtering + managed vector search (long-term retrieval only; no session/operational state). Elastic publishes reference architectures (three-index, hybrid retrieval, supersession, decay, DLS isolation) and an Agent Builder "conversation context store" (GA 2026) but no standalone memory product. Qdrant/SingleStore provide vector/hybrid storage with payload/metadata filtering; memory logic is DIY.

## Architecture Comparison: The Layered Pattern

The canonical stack is **Application → Agent Framework → Memory SDK → Memory Engine → Storage**. It instantiates differently:

- **Mem0**: App → any framework → Mem0 SDK (extraction/consolidation/retrieval pipeline) → Mem0 memory engine → hybrid vector+graph+KV store. The memory engine is a distinct service; the framework is interchangeable.
- **LangGraph/LangMem**: App → LangGraph (agent-as-graph, checkpointers for short-term) → LangMem SDK (Memory Manager) → BaseStore abstraction → Postgres/Redis/vector backend. Memory engine and storage are pluggable; short-term is native graph state.
- **Letta/MemGPT**: App → Letta runtime (agent + memory fused) → memory blocks compiled into the context window → PostgreSQL+pgvector. Here "Memory SDK" and "Memory Engine" collapse into the runtime; the agent itself is the memory manager.
- **Bedrock AgentCore Memory**: App → any framework/Strands → AgentCore Memory API (CreateEvent/RetrieveMemoryRecords) → managed extraction+consolidation service (async) → AWS-managed store (KMS-encrypted). The engine is fully managed and opaque.
- **Vertex AI Memory Bank**: App → ADK/LangGraph → Memory Bank API over Sessions → Gemini-based extraction engine → managed store. Extraction runs remotely and asynchronously.
- **Oracle / Neo4j (converged-DB)**: App → any framework → thin memory SDK → the database *is* the memory engine (Oracle AI Database's vector+relational+graph+JSON; Neo4j's graph). Storage, engine, governance, and audit collapse into one governed substrate — the architectural inverse of the Mem0 "separate service" model.

The key architectural axis: **where does the "memory engine" live** — in a standalone service (Mem0, Zep), fused into the agent runtime (Letta), managed by the cloud (Bedrock, Vertex), or absorbed into the database (Oracle, Neo4j, Redis, Couchbase)? And **who decides what to remember** — passive async extraction, agent self-editing, or developer-controlled writes?

## Feature Matrix

Legend: ✅ native/verified · ◐ partial or via integration · ❌ not present/not applicable · ? unverified.

| Capability | Mem0 | LangGraph/LangMem | Letta | Bedrock AgentCore | Vertex Memory Bank | Oracle AI Agent Memory | Zep/Graphiti | Redis Iris | CrewAI | Agno |
|---|---|---|---|---|---|---|---|---|---|---|
| Memory CRUD | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Namespaces/scopes | ✅ | ✅ | ◐ | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | ✅ |
| Memory graph | ✅ (Mem0ᵍ) | ◐ | ◐ | ❌ | ❌ | ✅ | ✅ | ❌ | ◐(entity) | ❌ |
| Vector search | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ |
| Hybrid search | ✅ | ◐ | ◐ | ? | ◐ | ✅ | ✅ | ✅ | ❌ | ◐ |
| Keyword search | ✅ | ◐ | ✅ | ? | ❌ | ✅ | ✅ | ✅ | ❌ | ◐ |
| Metadata filters | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | ✅ |
| TTL/expiration | ◐ | ◐ | ❌ | ✅ (≤365d) | ? | ✅ | ✅(bitemporal) | ✅ | ❌ | ❌ |
| Consolidation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ |
| Promotion (STM→LTM) | ✅ | ◐ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ |
| Decay | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ✅(invalidation) | ✅ | ❌ | ❌ |
| Reflection | ❌ | ◐ | ✅(sleep-time) | ❌ | ❌ | ◐ | ✅(observations) | ❌ | ❌ | ❌ |
| Scoring/ranking | ✅ | ◐ | ◐ | ✅ | ✅ | ✅ | ✅(RRF/MMR) | ✅ | ✅(composite) | ◐ |
| Cross-session memory | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Cross-agent memory | ◐ | ◐ | ✅(shared blocks) | ◐ | ◐ | ✅ | ✅ | ✅ | ◐ | ✅(team) |
| Cross-framework | ✅ | ❌ | ❌ | ◐ | ◐(ADK/LG/Crew) | ✅ | ✅ | ✅ | ❌ | ❌ |
| Cross-tenant isolation | ✅ | ◐ | ◐ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ◐ |
| RBAC | ◐ | ❌ | ◐ | ✅(IAM) | ✅(IAM) | ✅ | ✅ | ✅ | ❌ | ❌ |
| Encryption | ✅ | ◐ | ◐ | ✅(KMS) | ✅ | ✅ | ✅ | ✅ | ❌ | ◐ |
| PII detection | ◐ | ❌ | ❌ | ◐ | ◐ | ◐ | ◐ | ◐ | ❌ | ❌ |
| Audit | ✅ | ❌ | ◐ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Tracing/OTel | ◐ | ✅(LangSmith) | ◐ | ✅(CloudWatch) | ✅ | ◐ | ◐ | ◐ | ◐ | ✅(evals) |
| Knowledge graph | ✅ | ◐ | ◐ | ❌ | ❌ | ✅ | ✅ | ❌ | ◐ | ❌ |
| Multi-modal | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ |
| MCP support | ✅ | ◐ | ✅ | ✅(Gateway) | ◐ | ◐ | ✅ | ✅ | ◐ | ✅ |
| A2A support | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ |
| Import/export | ✅ | ◐ | ✅ | ◐ | ◐ | ✅ | ✅ | ✅ | ◐ | ◐ |
| Compliance (SOC2/HIPAA/etc.) | ✅(SOC2 T1,HIPAA) | ◐ | ◐ | ✅(AWS) | ✅(GCP) | ✅(Oracle) | ✅ | ✅ | ❌ | ❌ |
| Cloud-native/K8s | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ |
| HA/backup/DR | ◐ | ◐ | ◐ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ◐ |

Items that could not be reliably verified across most platforms and are flagged as unknown: memory snapshots/rollback/replay, offline sync, IBM z support, ARM64 support (beyond generic container support), FedRAMP status (except AWS/GCP/Azure inherited), agent personas beyond Letta's persona blocks, and formal "memory marketplace" features (none exist).

## Research Papers

- **MemGPT** (Packer et al., 2023, arXiv 2310.08560). OS-inspired virtual context management: LLM pages data between fixed "main context" and external storage via function calls. Core innovation: self-editing hierarchical memory. Strength: unbounded effective context; foundation for Letta. Weakness: per-operation LLM latency/cost, tool-calling dependency. Enterprise applicability: high (productionized as Letta).

- **Zep: A Temporal Knowledge Graph Architecture** (Rasmussen et al., 2025, arXiv 2501.13956). Graphiti engine, bi-temporal edges, three subgraphs. Innovation: fact invalidation + temporal reasoning + provenance. Strength: enterprise accuracy/auditability (94.8% DMR, +18.5% LongMemEval). Weakness: graph-DB operational complexity. Applicability: very high for regulated, temporally-sensitive domains.

- **Mem0** (Chhikara et al., 2025, arXiv 2504.19413). Production memory pipeline + graph variant. Innovation: scalable extraction/consolidation with low latency/cost. Strength: production-ready, framework-agnostic. Weakness: weak staleness handling, no temporal validity. Applicability: high for personalization.

- **MIRIX** (Wang & Chen, 2025, arXiv 2507.07957). Modular multi-agent memory with six types (Core, Episodic, Semantic, Procedural, Resource, Knowledge Vault) coordinated by a Meta Memory Manager; multimodal (screenshots). Innovation: multi-agent memory coordination + multimodal. Results: 85.4% LOCOMO; 35% over RAG with 99.9% less storage on ScreenshotVQA. Strength: richest type system, multimodal. Weakness: complexity; research-grade. Applicability: emerging — signals the future of multi-agent enterprise memory.

- **MemoryOS** (Kang et al., 2025, arXiv 2506.06326). Memory OS with four modules (storage/update/retrieval/generation) and three tiers (STM/MTM/LPM) with FIFO dialogue-chain and heat-based segmented-page replacement. Innovation: OS memory-management principles (paging, heat-based eviction). Strength: principled lifecycle. Weakness: research prototype. Applicability: conceptual blueprint for tiered memory.

- **LightMem** (Fang et al., 2025, arXiv 2510.18866). Atkinson-Shiffrin-inspired three-stage (sensory pre-compression/filtering, topic-aware STM, sleep-time LTM update). Innovation: efficiency — 10-38× token reduction, 3.6-30× fewer API calls, +2-29% accuracy on LongMemEval/LOCOMO. Strength: cost/latency. Weakness: newer, less battle-tested. Applicability: high for cost-sensitive production.

- **Generative Agents** (Park et al., 2023, arXiv 2304.03442). Memory stream + reflection + planning; retrieval scored by recency (exp. decay) × relevance (embedding) × importance (self-rated). Innovation: multi-signal scoring + reflection synthesis. Ablation: removing reflection degraded behavior within 48 simulated hours. Strength: influential scoring model. Weakness: simulation-focused. Applicability: the scoring formula underpins most later systems.

- **Reflexion** (Shinn et al., 2023, arXiv 2303.11366). Verbal self-critique stored and prepended on retry (no gradient updates): 91% vs 80% pass@1 on HumanEval. Innovation: procedural/reflective memory via natural-language feedback. Strength: simple, effective. Weakness: task-specific. Applicability: high for self-improving/coding agents.

- **Voyager** (Wang et al., 2023, arXiv 2305.16291). Skill library as procedural memory in Minecraft; ablation without skill library lost ~15× tech-tree milestone speed. Innovation: reusable executable skills as memory. Applicability: strong for tool/workflow-learning agents.

- **CAMEL** (Li et al., 2023, arXiv 2303.17760). Role-playing communicative multi-agent framework; memory via conversation history. Applicability: foundational for multi-agent, less a memory system per se.

- **SELFMEM / SelfMem** (arXiv 2607.03726, 2026 variant). Agent-controlled self-optimizing memory construction; best quality-cost trade-off at 1M-token scale vs RAG/compression baselines. Innovation: agent selectively constructs/revises memory. Applicability: promising for long-horizon.

- **LongMem / LongMemEval** (Wu et al., arXiv 2410.10813, ICLR 2025). LongMemEval is the standard 500-question benchmark for five long-term memory abilities (extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention); long-context LLMs drop 30%–60% and commercial systems (e.g., GPT-4o) achieve only 30%–70% accuracy across sustained interactions. Central to how vendors (Oracle, Zep) now report results.

Additional systems seen in recent literature: A-Mem (Zettelkasten-style agentic notes), MemoryBank (Ebbinghaus forgetting curve), NEMORI, SimpleMem, HiMem — signaling rapid academic proliferation.

## Industry Trends: 2023 → 2026 (verified) and 2027-2030 (predictions)

**2023 — Foundations.** MemGPT introduces OS-style paging; Generative Agents establish memory-stream + reflection + multi-signal retrieval; Reflexion and Voyager show reflective and procedural memory. Memory = mostly conversation buffers + RAG.

**2024 — Frameworks embed memory.** LangGraph checkpointers/stores, CrewAI's four memory types, Zep/Graphiti's temporal knowledge graph (paper Jan 2025 but built through 2024). LongMemEval published (Oct 2024) as a shared yardstick. Memory becomes a first-class framework concern.

**2025 — Managed memory arrives + cognitive taxonomy converges.** Mem0 paper (April), Vertex Memory Bank (public preview, July), Bedrock AgentCore Memory (preview), Anthropic Memory Tool (Sept 29), MIRIX/MemoryOS/LightMem papers, LangMem SDK. Microsoft consolidates AutoGen+SK into Agent Framework (Oct). The episodic/semantic/procedural taxonomy becomes near-universal.

**2026 — Enterprise/governed-substrate era.** Microsoft Agent Framework 1.0 GA (April 3); Oracle AI Agent Memory (SDK on PyPI, GA expected CY2026); Redis Iris (May); Couchbase AI Data Plane (July GA); Weaviate Engram (June GA); Neo4j Agent Memory Labs SDK; IBM watsonx Orchestrate control plane (Think 2026, May). Focus shifts to governance, isolation, audit/erasure, temporal fact modeling, and bounded per-turn cost.

**Predictions 2027-2030 (informed, clearly labeled as speculation):**
- *Standardization*: An MCP-style open memory-interchange standard likely emerges (2027-2028), driven by portability pain; expect a "memory resource" MCP extension or an A2A memory-sharing profile. **Prediction.**
- *Federation*: Cross-cloud/cross-vendor memory federation (query Oracle + Bedrock + Vertex memory through one interface) will be attempted but remain immature through 2028. **Prediction.**
- *Consolidation*: The standalone memory-SDK layer (Mem0, Zep) faces margin pressure as hyperscalers and databases absorb memory as a feature; expect acquisitions. **Prediction.**
- *Self-improving memory*: RL-on-episodic-memory and meta-evolution of memory systems (research already appearing: MemRL, MemEvolve) move toward production by ~2028. **Prediction.**
- *Multimodal + agentic memory marketplaces*: MIRIX-style multimodal memory mainstreams; nascent "memory marketplace" concepts (shareable/purchasable domain memories) may appear but face IP/privacy hurdles. **Speculative.**

## Competitive SWOT (Agent-Memory Space)

**Oracle** — *S*: only vendor with truly converged (vector+relational+graph+JSON) governed memory substrate; strong benchmark (93.8% LongMemEval); deep enterprise data-governance story; framework-agnostic SDK. *W*: Python-only initially; GA still CY2026; perceived Oracle lock-in; smaller AI-developer mindshare. *O*: existing Oracle-DB installed base migrating agents to same governed backend. *T*: hyperscaler bundling; open-source momentum (Mem0).

**Microsoft** — *S*: Agent Framework 1.0 GA, unifies AutoGen+SK; pluggable memory + context providers; Azure Managed Redis; deep enterprise/Azure integration; MIT license. *W*: memory is relatively thin (KV + vector via providers) vs specialized engines; recent consolidation churn. *O*: Foundry ecosystem, Copilot Studio. *T*: developers preferring specialized memory layers.

**Google** — *S*: Memory Bank managed + Gemini extraction + contradiction resolution; ADK; multi-framework support; express free tier. *W*: preview maturity; GCP-centric; long-term memory only (sessions separate). *O*: Gemini's long-context + memory combo. *T*: AWS/Oracle enterprise depth.

**Amazon** — *S*: AgentCore Memory managed, deep AWS integration, KMS, strategies, 365-day retention, IAM/CloudWatch. *W*: preview; opaque managed engine; no graph memory; AWS lock-in. *O*: massive AWS agent base. *T*: multi-cloud buyers wanting portability.

**OpenAI** — *S*: Sessions + Conversations API (no TTL) + sandbox file memory; huge developer adoption; simple. *W*: memory is basic (history + files), no governed enterprise memory, no graph/temporal. *O*: default choice for GPT builders. *T*: enterprises needing governance go elsewhere.

**Anthropic** — *S*: elegant client-side Memory Tool (developer owns storage), Context Editing (~84% token reduction), Managed Agents with versioned memory. *W*: Claude-only lock-in; beta; developer implements storage. *O*: strong coding-agent ecosystem (Claude Code). *T*: model-agnostic memory layers.

**Mem0** — *S*: largest standalone memory community (~48K stars), framework-agnostic, hybrid store, SOC2/HIPAA, strong benchmarks, $24M Series A. *W*: no temporal validity, staleness handling weak, native multi-agent shared memory limited. *O*: the neutral cross-framework/cross-model memory layer. *T*: hyperscaler/database absorption; commoditization.

**LangChain (LangGraph/LangMem)** — *S*: dominant framework distribution, LangSmith observability, checkpointers+store+LangMem, huge adoption. *W*: LangMem pre-1.0; memory tied to LangChain ecosystem; not cross-framework. *O*: default for LangChain users. *T*: frameworks bundling their own memory; enterprise governance gaps.

**IBM** — *S*: watsonx Orchestrate control-plane/governance positioning; Db2/watsonx.data; strong regulated-industry trust; any-agent/any-framework catalog. *W*: **no dedicated agent-memory SDK today** — memory is Db2/Redis primitives + orchestration, not a memory substrate. *O*: govern-the-fleet narrative + existing enterprise accounts; could ship a watsonx memory service. *T*: Oracle/converged-DB rivals already shipping governed memory; falling behind on the memory-substrate race specifically.

## Gap Analysis: What No Vendor Has Satisfactorily Solved

1. **Memory governance** — audit/erasure primitives exist (Oracle, Bedrock, ServiceNow) but end-to-end lineage of *how a memory influenced a decision* is unsolved.
2. **Memory federation (cross-cloud)** — no vendor lets you query memory spanning Oracle + Bedrock + Vertex + on-prem through one governed interface. Wide open.
3. **Memory portability** — no standard export/interchange format; migrating from Mem0 → Letta → Bedrock means rewriting. (Oracle/Neo4j reduce this *within* their substrate; cross-vendor is unsolved.)
4. **Universal APIs/standards** — MCP and A2A touch tools/agent messaging, not memory schemas; no accepted "memory interchange" standard exists.
5. **Enterprise compliance** — SOC2/HIPAA/GDPR claims exist; FedRAMP/HIPAA-specific *memory* certifications, IBM z/ARM64 support, and data-residency guarantees are largely unverified or absent.
6. **Cost optimization** — Oracle/LightMem show bounded-token approaches, but there's no cross-platform cost-governance tooling; token cost of extraction/reflection is under-instrumented.
7. **Explainability** — why a memory was retrieved/ranked, and provenance of derived facts, is only partially addressed (Zep's provenance is the leader); no standard.
8. **Testing/benchmarking** — LongMemEval/LOCOMO/DMR are emerging but narrow (mostly conversational QA); no standard for procedural/multi-agent/multimodal memory quality.
9. **Memory quality scores** — no standard metric for "memory health" (staleness, contradiction density, redundancy).
10. **Memory freshness** — bi-temporal invalidation (Zep) is the exception; most systems handle staleness weakly (Mem0, LangGraph noted explicitly).
11. **Trust/safety/privacy** — PII detection in memory, poisoning defenses, and cross-tenant leakage prevention are nascent; "misevolving" self-editing agents are an emerging risk in the literature.
12. **Memory marketplace** — conceptual only; no production implementation; blocked by IP/privacy/provenance issues.

## Recommendations

**Staged, decision-ready guidance:**

1. **If you need cross-framework/model-neutral memory now** → adopt **Mem0** (managed or OSS) as the memory layer; it's the safest neutral bet with SOC2/HIPAA. *Threshold to reconsider*: if you need temporal fact validity or auditable provenance, add or switch to **Zep/Graphiti**.

2. **If you are already on a hyperscaler** → use the native managed service (Bedrock AgentCore Memory / Vertex Memory Bank / Microsoft Agent Framework). Lowest integration cost. *Threshold*: if multi-cloud portability becomes a hard requirement, insert a neutral layer (Mem0/LangMem) above the cloud store to avoid lock-in.

3. **If governance, audit, isolation, and data residency are paramount (regulated industries)** → prioritize a **converged-DB substrate**: **Oracle AI Agent Memory** (if Oracle-DB shop, once GA CY2026) or **Neo4j/Zep** for graph+temporal needs. *Threshold*: require the vendor to demonstrate per-record erasure, tenant isolation at the store layer, and audit before production.

4. **If you need self-editing, stateful, long-running agents with identity** → **Letta**; accept the latency/token overhead and Postgres/Docker operational cost.

5. **If accuracy on temporal/evolving facts matters most** → **Zep/Graphiti** (bi-temporal invalidation + provenance); benchmark against your data using **LongMemEval**.

6. **Always instrument**: adopt LongMemEval/LOCOMO-style evaluation on your own data before committing; measure per-turn token cost (target bounded, not linear-in-history); and validate deduplication/staleness handling, which is where most systems (Mem0, CrewAI, LangGraph) are weakest.

**Benchmarks/thresholds that should change the decision:** (a) if LongMemEval multi-session accuracy on your data <80%, add a temporal-graph layer; (b) if per-turn tokens grow linearly with history, enable summarization/compaction or switch platforms; (c) if you cannot produce a per-user erasure report, the platform fails enterprise governance; (d) if cross-tenant tests show leakage, isolation must be enforced at the store layer, not the app.

## Caveats

- **Recency/ambiguity flags**: Several offerings are in preview/beta (Bedrock AgentCore Memory, Vertex Memory Bank, Anthropic Memory Tool, Neo4j Agent Memory Labs SDK) — APIs and capabilities will change. Oracle AI Agent Memory GA is "expected CY2026"; treat SDK code samples as illustrative.
- **Vendor-reported benchmarks** (Oracle 93.8% LongMemEval, Zep 94.8% DMR, Mem0 V3 91.6 LOCOMO, MIRIX 85.4% LOCOMO, LightMem token reductions) come from the vendors/authors themselves under specific model/config conditions and are not independently reproduced here; verify on your workload.
- **The feature matrix** contains ◐/? cells where official documentation was ambiguous or unavailable; these are explicitly marked and should be verified against current docs before procurement. Multimodal, snapshot/rollback/replay, offline sync, IBM z/ARM64, and FedRAMP status were the least verifiable across platforms.
- **Neo4j Agent Memory** is a Neo4j Labs experimental/community project, not officially GA-backed; Graphiti is a Zep product (Neo4j is one backend).
- **Some sources are secondary** (Medium, vendor comparison blogs, marketing pages). Wherever possible, primary docs (official documentation, arXiv, GitHub) were prioritized; secondary sources are corroborative only. Claims about IBM/SAP/Snowflake memory rest partly on event coverage (Think 2026) and community patterns rather than dedicated product docs.
- Coverage of LlamaIndex, Haystack, Camel AI, OpenManus, Continue.dev, and AnythingLLM memory is thinner than the primary platforms due to source/time limits; treat their per-feature entries as directional.

### Key source links for the follow-up report
Official docs & products: docs.mem0.ai · github.com/mem0ai/mem0 · docs.letta.com · docs.langchain.com/oss/python/concepts/memory · langchain.com/blog/langmem-sdk-launch · docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html · cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/memory-bank/overview · learn.microsoft.com/agent-framework · devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0 · oracle.com/database/ai-agent-memory · docs.oracle.com/en/database/oracle/agent-memory/26.4 · pypi.org/project/oracleagentmemory · openai.github.io/openai-agents-python/sandbox/memory · redis.github.io/agent-memory-server · github.com/redis/agent-memory-server · docs.couchbase.com/ai/build/agent-memory · github.com/neo4j-labs/agent-memory · docs.nvidia.com/nemo/agent-toolkit · docs.databricks.com (…/agent-framework/stateful-agents) · docs.crewai.com · docs.agno.com. Papers (arXiv): 2310.08560 (MemGPT) · 2501.13956 (Zep) · 2504.19413 (Mem0) · 2507.07957 (MIRIX) · 2506.06326 (MemoryOS) · 2510.18866 (LightMem) · 2304.03442 (Generative Agents) · 2303.11366 (Reflexion) · 2305.16291 (Voyager) · 2303.17760 (CAMEL) · 2410.10813 (LongMemEval).
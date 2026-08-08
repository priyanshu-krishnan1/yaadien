# Advanced Agent Memory: System Design, Algorithms, and Research Grounding

**A design research document for `agent-memory-sdk` v2**

Status: research / proposal · Date: 2026-08-08 · Scope: extends the existing Db2-backed SDK

---

## 0. How to read this document

This is a **design research document**, not an implementation plan. Every non-obvious
design choice carries a citation to the primary source that motivates it, so that
each decision can be independently re-derived or contested.

The structure is:

| Part | Question it answers |
|---|---|
| I | What is agent memory, formally? What does the literature actually agree on? |
| II | What are the layers of the system, and what invariant does each own? |
| III | What data structures and algorithms make each layer work, and at what cost? |
| IV | How does a single `recall()` actually execute, end to end? |
| V | How do memories change over time — consolidate, decay, contradict, expire? |
| VI | How do we keep an evolving memory safe, private, and auditable? |
| VII | How do we know any of it works? |
| VIII | What is the concrete delta from today's SDK, in dependency order? |

A short-hand used throughout: **`R`** = a memory record, **`S`** = a `MemoryScope`
(`tenant_id`/`agent_id`/`user_id`/`thread_id`), **`N`** = records in scope,
**`d`** = embedding dimension, **`k`** = requested result count.

---

## Part I — Foundations

### 1.1 The field has converged on a taxonomy; it has not converged on a system

The most recent broad survey, *Memory in the Age of AI Agents*
([arXiv:2512.13564](https://arxiv.org/abs/2512.13564), 47 authors, Dec 2025 / rev.
Jan 2026), makes the state of the field explicit: research is *fragmented*, and
"traditional taxonomies such as long/short-term memory have proven insufficient."
It proposes a three-axis decomposition that this design adopts wholesale:

- **Forms** — *token-level* (text in context / retrievable stores), *parametric*
  (weights), *latent* (KV-cache, compressed states).
- **Functions** — *factual*, *experiential*, *working*.
- **Dynamics** — *formation*, *evolution*, *retrieval*.

The older and still load-bearing framework is **CoALA**
([arXiv:2309.02427](https://arxiv.org/abs/2309.02427), Sumers, Yao, Narasimhan &
Griffiths, TMLR), which maps agent memory onto Tulving's trichotomy: working /
episodic / semantic / procedural. **Our existing five types are already a
CoALA-conformant refinement** — `working`, `episodic`, `semantic_facts`,
`entity_profiles`, `procedural` — with `entity_profiles` splitting entity-anchored
semantics out of the flat semantic store. That is a real advantage over most
implementations and should be preserved, not rebuilt.

> **Design position.** Keep the CoALA-derived typed schema as the *storage*
> contract. Add the survey's *forms* axis (§3.6, latent/KV memory) and *dynamics*
> axis (Part V) as orthogonal concerns layered on top, rather than as new types.

**Further reading**

- [Memory in the Age of AI Agents (survey, 2025/26)](https://arxiv.org/abs/2512.13564) — start here; §7 has the benchmark and framework tables.
- [Agent-Memory-Paper-List](https://github.com/Shichun-Liu/Agent-Memory-Paper-List) — the survey's continuously-updated companion bibliography.
- [Cognitive Architectures for Language Agents (CoALA)](https://arxiv.org/abs/2309.02427) — the canonical taxonomy.
- [LLM Agent Memory: A Survey from a Unified Representation–Management Perspective](https://openreview.net/forum?id=KPs1EgGKcT) — organises methods by construction / update / query stages.
- [Always-On Agents: Persistent Memory, State, and Governance in LLM Agents](https://arxiv.org/pdf/2606.30306) — governance-first framing, closest to this SDK's thesis.

### 1.2 The five reference systems worth stealing from

| System | Core idea | What we should take | Source |
|---|---|---|---|
| **MemGPT / Letta** | OS-style virtual context: main context ↔ external store with agent-issued paging function calls, interrupt-driven control flow | *Self-editing memory* — the agent, not the framework, decides what gets promoted | [arXiv:2310.08560](https://arxiv.org/abs/2310.08560) |
| **Generative Agents** | Memory stream scored by `α_rec·recency + α_imp·importance + α_rel·relevance`; periodic *reflection* synthesises higher-level nodes stored back in the stream | The three-term scoring function, and reflection-as-a-write-path | [Park et al., UIST '23](https://dl.acm.org/doi/fullHtml/10.1145/3586183.3606763) |
| **HippoRAG** | Neocortex (LLM) + parahippocampal encoder + hippocampal open KG; retrieval = **Personalized PageRank** over an entity graph seeded by query entities | Graph-propagated retrieval for multi-hop, at 10–30× lower cost than iterative retrieval | [NeurIPS '24](https://arxiv.org/abs/2405.14831) · [HippoRAG 2](https://arxiv.org/pdf/2502.14802) |
| **Zep / Graphiti** | **Bi-temporal** knowledge graph: four timestamps per edge (`t_created`, `t_expired`, `t_valid`, `t_invalid`); contradictions *invalidate*, never delete | The temporal model — this is the single highest-value upgrade for us | [arXiv:2501.13956](https://arxiv.org/abs/2501.13956) |
| **A-MEM** | Zettelkasten-style: each new note carries generated keywords/tags/context, is auto-linked, and *triggers evolution of existing notes* | Memory evolution as a first-class write side-effect | [arXiv:2502.12110](https://arxiv.org/abs/2502.12110) |

Two more that matter for cost/production posture:

- **Mem0** ([arXiv:2504.19413](https://arxiv.org/abs/2504.19413)) — extraction +
  consolidation pipeline; reports ~91% lower p95 latency and >90% token savings vs.
  full-context baselines on LoCoMo, with a graph variant (`Mem0g`) adding ~2%.
  The lesson is that **the win is mostly in *not* sending everything**, not in
  exotic retrieval.
- **Agent Workflow Memory** ([arXiv:2409.07429](https://arxiv.org/abs/2409.07429),
  ICML '25) — induces reusable *workflows* from successful trajectories; +24.6%
  (Mind2Web) / +51.1% (WebArena) relative success. This is the research backing
  for making our `procedural` type actually earn its place (§5.5).

### 1.3 The constraint that justifies the whole system

Long context does not remove the need for memory. Three independent results:

1. **Lost-in-the-middle** — accuracy is a U-shaped function of position; >30%
   degradation when the needle sits mid-context, replicated across six model
   families ([Liu et al.](https://arxiv.org/abs/2307.03172)). Mechanistically
   attributed in part to RoPE's long-term decay.
2. **Context rot** — every one of 18 frontier models tested degrades monotonically
   with input length, even on trivially retrievable content
   ([Chroma technical report](https://research.trychroma.com/context-rot)).
3. **LongMemEval** — commercial assistants show a ~30% accuracy drop on sustained
   interactive memory ([arXiv:2410.10813](https://arxiv.org/abs/2410.10813)).

> **Design position.** The optimisation target is *not* recall@k. It is
> **answer accuracy per token of assembled context**. Every component below is
> justified by that objective function, and §7 makes it measurable.

---

## Part II — System design

### 2.1 Seven planes

The current SDK is essentially planes 1, 2, and 6 with a thin 3. v2 makes all seven
explicit, each owning exactly one invariant.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 7. CONTEXT ASSEMBLY PLANE                                                │
│    budget-constrained selection · MMR diversification · position-aware   │
│    packing · provenance-annotated rendering                              │
│    INVARIANT: output fits the token budget and is ordered for the U-curve│
├──────────────────────────────────────────────────────────────────────────┤
│ 6. GOVERNANCE PLANE                                                      │
│    scope isolation · provenance/trust tiers · TTL · erasure · audit log  │
│    INVARIANT: no read or write crosses a scope boundary                  │
├──────────────────────────────────────────────────────────────────────────┤
│ 5. EVOLUTION PLANE  (offline / sleep-time)                               │
│    consolidation · reflection · entity resolution · community summaries  │
│    decay & eviction · contradiction reconciliation                       │
│    INVARIANT: every derived record links to its sources (no orphans)     │
├──────────────────────────────────────────────────────────────────────────┤
│ 4. REASONING PLANE                                                       │
│    temporal KG (bi-temporal edges) · entity graph · PPR propagation      │
│    INVARIANT: no fact is asserted outside its valid-time interval        │
├──────────────────────────────────────────────────────────────────────────┤
│ 3. RETRIEVAL PLANE                                                       │
│    multi-index candidate generation → fusion (RRF) → rerank → dedup      │
│    INVARIANT: every candidate carries a calibrated, comparable score     │
├──────────────────────────────────────────────────────────────────────────┤
│ 2. INDEX PLANE                                                           │
│    ANN (HNSW/DiskANN) · sparse/BM25 · temporal interval · metadata       │
│    INVARIANT: indexes are consistent with the store at read-your-writes  │
├──────────────────────────────────────────────────────────────────────────┤
│ 1. STORAGE PLANE                                                         │
│    typed rows · MVCC versioning · content-hash identity · chunk table    │
│    INVARIANT: writes are idempotent and linearizable per record          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 2.2 What each plane changes relative to today

| Plane | Today | v2 |
|---|---|---|
| 1 Storage | 6 Db2 tables, soft delete, `version` optimistic concurrency, content-hash dedup | + bi-temporal columns, + trust tier, + `derived_from` edge table, + tiered storage (hot/warm/cold) |
| 2 Index | Db2 `CREATE VECTOR INDEX` per table | + sparse/BM25 index, + temporal B-tree on `(valid_from, valid_to)`, + filtered-ANN strategy selection |
| 3 Retrieval | vector search, optional RRF with Python keyword ranker, chunk resolution | + query planning, + cross-encoder rerank, + Matryoshka two-stage, + MMR |
| 4 Reasoning | *(absent)* | entity graph + Personalized PageRank + bi-temporal validity filter |
| 5 Evolution | synchronous `Consolidator` + `Reconciler` + throttle | + sleep-time worker, + reflection, + decay/eviction policy, + entity resolution, + community summarisation |
| 6 Governance | scope predicates in SQL, TTL, `forget()`, `erase_all()`, export/import | + provenance trust tiers with promotion gates, + poisoning defences, + tamper-evident audit chain |
| 7 Assembly | `get_context_card()` with pluggable `Summarizer` | + token-budget solver, + MMR, + U-curve-aware ordering, + citation rendering |

---

## Part III — Data structures and algorithms

Notation: `N` records in scope, `d` dims, `k` results, `M` HNSW out-degree,
`efSearch` = search beam width.

### 3.1 The vector index: HNSW, and when it stops being the right answer

**HNSW** ([Malkov & Yashunin, arXiv:1603.09320](https://arxiv.org/abs/1603.09320);
TPAMI 2020) builds a multi-layer proximity graph where a node's top layer is drawn
from an exponentially decaying distribution, giving skip-list-like scale separation.

- Build: `O(N·log N·M·d)`; Query: `O(efSearch · M · d)`, empirically `~O(log N)` hops.
- Memory: `O(N·(d·4 + M·2·8))` bytes for fp32 + bidirectional links.
- Weakness: **in-memory only**, and deletes are tombstones — recall degrades under
  churn until rebuild. Agent memory is *high-churn by construction*.

**DiskANN / Vamana** ([Subramanya et al., NeurIPS
'19](https://www.microsoft.com/en-us/research/publication/diskann-fast-accurate-billion-point-nearest-neighbor-search-on-a-single-node/))
solves the scale problem: full-precision vectors + graph on SSD, PQ-compressed
vectors in RAM for approximate distances, full-precision reads only for final
rerank. A billion points on 64 GB RAM, >5000 QPS at <3 ms mean, 95%+ recall@1.
**FreshDiskANN** extends it to streaming inserts/deletes — which is the property
agent memory actually needs.

> **⚠️ CORRECTION (2026-08-08) — this section's original design position was wrong.**
> It proposed a `VectorIndex` protocol with pluggable `hnswlib` / `diskann`
> backends. **Db2's vector index already *is* DiskANN.** Db2 12.1.5 (GA 25 June
> 2026) ships DiskANN-powered vector indexing; the catalog exposes it as
> `SYSCAT.INDEXES.INDEXTYPE = 'VANN'` and EXPLAIN labels the access `VECIDX`
> ([IBM announcement](https://www.ibm.com/new/announcements/ibm-db2-12-1-5-now-available-bringing-ai-to-where-your-mission-critical-data-already-lives);
> [hands-on write-up](https://data-henrik.de/2026/05/db2-vector-indexes-nearest-neighbor/)).
>
> We consume that index; we cannot tune, replace, or contribute to its internals.
> There is no `efSearch`, no `M`, no rebuild policy we control — so a
> pluggable-backend abstraction would be a seam with nothing to put behind it.
>
> **Revised design position.** All remaining leverage is on our side of the
> boundary: making sure Db2's DiskANN is actually *engaged* (a non-sargable
> predicate can silently cost you the index), keeping sentinel rows out of the
> indexed column, shaping predicates and candidate inflation so filtered searches
> stay on the index path, and measuring recall when they don't. Db2 also hands us
> a free recall harness — the same query under `FETCH EXACT` and `FETCH APPROX`
> gives ground truth and approximation over identical data. Two further
> constraints worth recording: the vector index is **only used when the column is
> `NOT NULL`**, and on small tables the optimizer legitimately prefers a table
> scan and needs `OPTGUIDELINES` to use the index at all — so a green test suite
> on small fixtures is not evidence the index path works.
>
> This is tracked as **EPIC-28** on the board; see `AI_NATIVE_AGILE.md` §3 on
> retraction-in-place.

**Quantization.** When RAM becomes the binding constraint, prefer **RaBitQ**
([SIGMOD '24, arXiv:2405.12497](https://arxiv.org/abs/2405.12497)) over PQ/OPQ:
it quantizes `d`-dim vectors to `d` bits (vs. `2d` for PQ defaults), gives an
*unbiased* distance estimator with a **sharp theoretical error bound**, where PQ
has none and "is observed to fail disastrously on some real-world datasets."

**Filtered search is the actual hard problem.** Every read in this SDK is
scope-filtered, and often metadata-filtered on top. Three regimes:

| Selectivity of predicate | Best strategy | Why |
|---|---|---|
| Very low (<0.1% pass) | **Pre-filter → brute force** | Candidate set is small; exact scan beats graph traversal |
| Middle (0.1%–10%) | **Filtered-DiskANN** or **ACORN** | Naive post-filtering blows up `efSearch`; graph becomes disconnected under the predicate |
| High (>10% pass) | **Post-filter over standard ANN** with inflated `k` | Predicate barely prunes; standard index is near-optimal |

- [Filtered-DiskANN (WWW '23)](https://harsha-simhadri.org/pubs/Filtered-DiskANN23.pdf) — label-aware RNG pruning: an edge is pruned only if the RNG condition holds *and* the intermediate node's label set covers both endpoints.
- [ACORN (SIGMOD '24)](https://dl.acm.org/doi/10.1145/3654923) — predicate-agnostic HNSW variant with γ-controlled edge inflation; higher predicate selectivity requires higher γ to keep traversal navigable.
- [Filtered ANN: a unified benchmark](https://arxiv.org/html/2509.07789v1) — the honest conclusion: *no single method wins across scenarios*, hence the routing table above rather than a single choice.

**Concretely for us:** scope columns (`tenant_id`, `agent_id`, `user_id`,
`thread_id`) are extremely high-selectivity. That argues strongly for
**partition-per-scope-prefix indexes** (an index per `(tenant, agent)` rather than
one global index with a filter), which turns the hardest filtered-ANN case into
the easy unfiltered case. This is a storage-layout decision, and it is the single
highest-leverage index-plane change.

### 3.2 Sparse retrieval is not optional

Our current "hybrid" is vector + a zero-infrastructure Python keyword-overlap
ranker. That was the right call for a dependency-light v1, but it under-performs
on exactly the queries agent memory gets most: proper nouns, IDs, rare tokens,
negations.

Two upgrade paths, in increasing order of cost:

1. **BM25 in Db2** via a text index or a term-frequency side table. Standard,
   cheap, and complementary to dense in a well-understood way.
2. **Learned sparse** (SPLADE-family) — dense-quality semantics in an invertible-index-friendly format. Higher indexing cost, no ANN infrastructure.

**Contextual Retrieval** ([Anthropic,
2024](https://www.anthropic.com/engineering/contextual-retrieval)) is the highest
ROI item in this whole section, and it composes with our existing chunker: prepend
an LLM-generated 50–100 token situating context to each chunk *before* embedding
and *before* BM25 indexing. Reported: −35% retrieval failure from contextual
embeddings alone; −49% combined with contextual BM25; −67% with reranking on top.
Cost is amortised to ~$1.02 per million document tokens via prompt caching.

> This maps directly onto `repositories/chunks.py` — the chunk row already exists;
> it gains a `context_prefix` column and the embedding is taken over
> `context_prefix + content`.

### 3.3 Fusion: RRF, and why the constant matters

**Reciprocal Rank Fusion** ([Cormack, Clarke & Büttcher, SIGIR
'09](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)) — already implemented
in `repositories/base.py::_rrf_fuse`:

```
RRF(r) = Σ_{i ∈ retrievers}  w_i / (K + rank_i(r))
```

Properties that make it correct here: it is **score-scale invariant** (cosine
distance and BM25 scores are not comparable; ranks are), needs no training data,
and the paper shows it beating Condorcet Fuse and trained rank learners on LETOR 3.

- `K = 60` is the paper's value and a sane default. Lower `K` → sharper preference
  for top-ranked items; higher `K` → flatter, more democratic fusion.
- Add `w_i` per retriever — our current implementation should expose these so a
  scope can up-weight lexical retrieval for ID-heavy domains.
- Complexity: `O(Σ|L_i| · log Σ|L_i|)` — negligible relative to candidate generation.

### 3.4 Reranking, and the two-stage cost ladder

Candidate generation optimises recall; reranking optimises precision. Three tiers:

| Tier | Method | Cost per candidate | When |
|---|---|---|---|
| 0 | RRF only | ~0 | default, `k ≤ 10` |
| 1 | **Matryoshka two-stage** | 1 extra dot product | always, if the embedding model supports it |
| 2 | Cross-encoder / LLM reranker | 1 forward pass | high-stakes reads, `k` from 100 → 10 |

**Matryoshka Representation Learning** ([NeurIPS
'22](https://proceedings.neurips.cc/paper_files/paper/2022/file/c32319f4868da7613d78af9993100e42-Paper-Conference.pdf))
is nearly free architecture-level leverage: MRL-trained embeddings nest coarse
information in their leading dimensions, so you shortlist with the first 64–256
dims and rerank the shortlist with the full 768–3072. For a Db2 `VECTOR` column
this means **two columns** (`embedding_short`, `embedding_full`), an ANN index only
on the short one, and an exact rerank on the shortlist. Index memory drops ~4–12×;
recall loss is small and *measurable*.

**Late interaction** (ColBERTv2, [arXiv:2112.01488](https://arxiv.org/abs/2112.01488);
PLAID, [arXiv:2205.09707](https://arxiv.org/abs/2205.09707)) — per-token embeddings
scored by query-side MaxSim — is the quality ceiling, and PLAID's centroid-pruning
makes it 9–45× faster on CPU than vanilla ColBERTv2. It is listed here as the
*known upper bound* for a future optional adapter; the storage cost (one vector per
token) is real, and it should not be in the default path.

### 3.5 Diversity: MMR, because near-duplicates waste the budget

Agent memory is pathologically redundant — the same fact restated across sessions.
Top-`k` by score returns `k` paraphrases of one thing.

**Maximal Marginal Relevance** ([Carbonell & Goldstein, SIGIR
'98](https://www.cs.cmu.edu/~jgc/publication/The_Use_MMR_Diversity_Based_LTMIR_1998.pdf)):

```
MMR = argmax_{Rᵢ ∈ C\S} [ λ · sim(Rᵢ, q) − (1−λ) · max_{Rⱼ ∈ S} sim(Rᵢ, Rⱼ) ]
```

Greedy, `O(k · |C| · d)`, `λ ≈ 0.7` as a starting point. Applied *after* fusion,
*before* budget packing. This is a ~40-line addition with an outsized effect on
context-card quality per token.

### 3.6 Temporal indexing: the structure the current schema lacks

To answer "what did the user believe *as of last March*" you need interval
containment queries, not timestamp comparison. Options:

| Structure | Query | Build | Space | Notes |
|---|---|---|---|---|
| **Interval tree** (augmented RB-tree) | `O(log N + m)` | `O(N log N)` | `O(N)` | Textbook; CLRS §14.3 |
| **Segment tree** | `O(log N + m)` | `O(N log N)` | `O(N log N)` | Better for stabbing-heavy loads |
| **B-tree on `(valid_from, valid_to)`** | `O(log N + m)` w/ good selectivity | native | `O(N)` | **What Db2 gives us for free** |

> **Design position.** Do not build an interval tree. Add a composite index on
> `(scope_cols…, valid_from DESC, valid_to)` and express as-of queries as
> `valid_from <= :t AND (valid_to IS NULL OR valid_to > :t)`. This is the SQL:2011
> temporal-table pattern; Db2 supports system-period and application-period
> temporal tables natively, which is a strictly better fit than hand-rolling.

### 3.7 Cardinality and frequency sketches for the eviction policy

Eviction (§5.4) needs access statistics per record without a write on every read.
Two sublinear structures:

- **Count–Min Sketch** ([Cormode & Muthukrishnan, *J. Algorithms* 2005](https://doi.org/10.1016/j.jalgor.2003.12.001)) — `O(1)` update, `O(ε⁻¹ log δ⁻¹)` space, one-sided error. Tracks per-record retrieval frequency for the *recency/utility* term.
- **HyperLogLog** ([Flajolet, Fusy, Gandouet & Meunier, AofA 2007 / DMTCS](https://dmtcs.episciences.org/3545)) — distinct-query cardinality per memory in ~1.5 KB at ~2% error. Distinguishes "retrieved 100× by one query pattern" from "retrieved 100× by 100 different needs" — the latter is far more valuable and should survive eviction.

Both live in the process, flush periodically. Neither touches the hot write path.

### 3.8 Concurrency: MVCC now, CRDT only if you truly go multi-writer

The existing `version` column + `StaleWriteError` is optimistic concurrency
control, which is correct and sufficient for single-writer-per-record. Multi-agent
shared memory breaks that assumption.

- **Prefer MVCC + Db2 transactions.** Snapshot isolation gives read-your-writes and
  avoids the write-skew class most agent workloads hit.
- **CRDTs** ([Shapiro, Preguiça, Baquero & Zawirski, SSS 2011](https://link.springer.com/chapter/10.1007/978-3-642-24550-3_29))
  only if you need offline/partitioned agents converging without coordination. The
  natural fits: **OR-Set** for tag/label sets, **LWW-Register** for scalar profile
  fields, **G-Counter** for access counts. Do *not* CRDT the content field — text
  merge semantics for facts are semantically wrong; that's what reconciliation
  (§5.3) is for.

### 3.9 Latent memory (KV-cache) — the axis we don't have at all

The survey's *latent memory* form: cache the transformer KV state for a stable
memory prefix and reuse it across turns. This is the mechanism behind
cache-augmented generation and is what actually collapses the cost of a large,
slowly-changing memory block.

Requirement it imposes on us: **context cards must be prefix-stable**. If the
assembled card reshuffles on every turn, the cache never hits. Practical rule —
render the card in a fixed order (stable long-term block first, volatile working
block last), so the stable prefix is byte-identical across turns.

---

## Part IV — The retrieval pipeline

### 4.1 End-to-end path for `recall(q, S, budget)`

```
                    ┌─── query understanding ───┐
  query q ─────────►│ intent classify           │
                    │ entity/temporal extraction│
                    │ decomposition (multi-hop) │
                    │ HyDE (optional)           │
                    └────────────┬──────────────┘
                                 │ plan: {retrievers, filters, k_i, as_of}
      ┌──────────────┬───────────┼───────────┬──────────────────┐
      ▼              ▼           ▼           ▼                  ▼
  dense ANN      sparse/BM25  entity-graph  temporal          recency
  (short dim)                  PPR          interval scan     (working mem)
      │              │           │           │                  │
      └──────────────┴─────┬─────┴───────────┴──────────────────┘
                           ▼
                    RRF fusion (weighted, K=60)
                           ▼
                    Matryoshka full-dim exact rerank
                           ▼
                    cross-encoder rerank        [tier 2, optional]
                           ▼
                    bi-temporal validity filter  ← drops invalidated facts
                           ▼
                    trust-tier filter            ← drops unpromoted memories
                           ▼
                    MMR diversification (λ=0.7)
                           ▼
                    budget-constrained packing (§4.3)
                           ▼
                    provenance-annotated context card
```

### 4.2 Query understanding

Cheap, high-yield, and currently absent:

- **Intent routing.** A lookup ("what's my API key format") needs sparse-heavy
  retrieval; a synthesis question ("what does this user care about") needs
  community summaries (§5.6); a temporal question needs the as-of path. Route
  before you retrieve.
- **Temporal expression extraction.** "last quarter", "before the migration" →
  `as_of` / interval bounds. Without this, bi-temporal storage is inert.
- **Decomposition for multi-hop.** LongMemEval's cross-session-reasoning slice and
  LoCoMo's multi-hop slice are both decomposition-bound, not retrieval-bound.
- **HyDE** ([Gao et al., arXiv:2212.10496](https://arxiv.org/abs/2212.10496)) —
  embed a hypothetical *answer* rather than the question, closing the
  question/answer asymmetry in embedding space. One extra LLM call; use it only
  when the query is short and abstract.

### 4.3 Budget-constrained packing

Given candidates `{(Rᵢ, scoreᵢ, tokensᵢ)}` and budget `B`, choose a subset
maximising value. This is 0/1 knapsack — `O(n·B)` exactly, which is fine at our
scale but overkill.

**Recommendation:** greedy by score-density `scoreᵢ / tokensᵢ`, with
per-type floors from the existing `min_results_by_type` mechanism. Greedy is
`(1 − 1/e)`-optimal for the monotone submodular objective that MMR-style value
functions induce ([Nemhauser, Wolsey & Fisher,
1978](https://link.springer.com/article/10.1007/BF01588971)) — a strong enough
guarantee that exact knapsack is not worth the complexity.

**Ordering, not just selection.** Given lost-in-the-middle, place the highest-value
records at the **start and end** of the card and low-value in the middle — the
inverse of the U-curve. This is free.

### 4.4 Scoring: generalising Generative Agents

Extend the Park et al. three-term score with the terms we already have columns for:

```
score(R, q, t) = w_rel · cos(e_R, e_q)
               + w_rec · exp(−λ · Δt_last_access)
               + w_imp · importance(R)
               + w_cnf · confidence(R)          ← already in our schema
               + w_frq · log(1 + freq(R))       ← Count–Min (§3.7)
               − w_dec · decay(R)               ← §5.4
```

All weights per-scope-configurable, defaulting to Park's `α = 1` uniform. Critically:
**log the components alongside the result** so the weights can be fit against
LongMemEval rather than guessed. Our `benchmarks/scoring_weights.yaml` is already
the right home for this.

---

## Part V — Evolution: how memory changes

### 5.1 Sleep-time compute — the architectural unlock

**Sleep-time Compute** ([Lin et al., Letta,
arXiv:2504.13171](https://arxiv.org/abs/2504.13171)) formalises what our
`scripts/consolidate_pending.py` gestures at: when the agent is idle but still has
the context, it should be *reasoning about it offline* — anticipating queries,
precomputing derived quantities, rewriting and compressing memory. Raw context
becomes **learned context**, and test-time compute drops accordingly.

> **Design position.** Promote the background worker from an "escape hatch" to the
> **primary** consolidation path, with the synchronous consolidator kept only for
> read-your-writes-critical cases. Concretely: a durable job queue
> (`memory_jobs` table, `FOR UPDATE SKIP LOCKED` claim semantics) with job kinds
> `CONSOLIDATE`, `REFLECT`, `RESOLVE_ENTITIES`, `SUMMARISE_COMMUNITY`,
> `DECAY_SWEEP`, `REINDEX`.

### 5.2 Reflection and memory evolution

Two distinct write-paths, both currently missing:

- **Reflection** (Generative Agents): periodically, take the `n` most recent
  high-importance records, ask the model for the salient higher-level inferences,
  and write those back as first-class retrievable records with `derived_from`
  edges. Trigger on cumulative importance crossing a threshold, not on a timer.
- **Evolution** (A-MEM, [arXiv:2502.12110](https://arxiv.org/abs/2502.12110)): a
  new record can *update* the tags, context, and links of existing neighbours. The
  memory network refines itself rather than only accreting.

Both need the same primitive: a **`memory_edges` table** (`src_id`, `dst_id`,
`edge_type ∈ {DERIVED_FROM, SUPERSEDES, CONTRADICTS, ELABORATES, CO_OCCURS}`,
`weight`, `created_at`). This one table also unlocks Part IV's graph retriever and
§5.6's community summaries. **It is the highest-leverage schema addition in this
document.**

### 5.3 Contradiction: invalidate, never delete

Zep/Graphiti's bi-temporal model ([arXiv:2501.13956](https://arxiv.org/abs/2501.13956))
tracks four timestamps per fact:

| Timestamp | Meaning |
|---|---|
| `t_created` | when the system learned it (transaction time, start) |
| `t_expired` | when the system stopped believing it (transaction time, end) |
| `t_valid` | when it became true in the world (valid time, start) |
| `t_invalid` | when it stopped being true in the world (valid time, end) |

This is exactly Snodgrass's bitemporal model, standardised in SQL:2011
([TSQL2 data model](https://people.cs.aau.dk/~csj/Thesis/pdf/chapter12.pdf);
[Fowler's practitioner treatment](https://martinfowler.com/articles/bitemporal-history.html)).

Why it matters concretely: "I live in Berlin" (learned Jan, valid from 2019) and
"I live in Lisbon" (learned Mar, valid from Feb) are **not** a contradiction to be
resolved by recency — they are two facts with disjoint valid intervals. A
recency-only reconciler gets "where did they live in 2020?" wrong. Our existing
`Reconciler` + soft-supersession is the right *shape*; it needs the valid-time
dimension to be correct.

**Migration:** add `valid_from`, `valid_to` to fact/profile tables; default
`valid_from = created_at`, `valid_to = NULL`. Existing supersession sets
`valid_to` on the loser instead of only flagging it. Backwards compatible.

### 5.4 Forgetting: a real decay function

Most systems skip this and grow without bound. The psychology has a well-tested
answer.

Ebbinghaus's exponential `R(t) = e^(−t/S)` is the classic form, but the modern
**FSRS/DSR** model (Difficulty–Stability–Retrievability) replaced exponential with
a **power law** in v4 because it fit real review logs measurably better
([FSRS algorithm docs](https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm);
[history](https://expertium.github.io/History.html)). The reason is mechanistically
interesting and directly relevant: a power law has a *falling hazard rate* — a
memory that has already survived a long interval is more durable going forward.
That is precisely the behaviour you want for agent memory, where a fact retrieved
across many sessions should become progressively harder to evict.

```python
# Retrievability under the DSR power law
def retrievability(elapsed_days: float, stability: float) -> float:
    return (1.0 + FACTOR * elapsed_days / stability) ** DECAY   # DECAY < 0

# Stability grows on each successful retrieval (spacing effect)
def on_retrieval(stability: float, retrievability: float) -> float:
    return stability * (1.0 + GROWTH * (1.0 - retrievability))
```

**Eviction ladder** — never hard-delete on decay alone:

```
hot (indexed, full fidelity)
  │  R < θ₁ and freq low
  ▼
warm (indexed, summary only — original archived)
  │  R < θ₂
  ▼
cold (archived, not indexed, restorable)
  │  TTL expiry or explicit erasure
  ▼
purged (tombstone + audit record retained)
```

`forget()` (user-initiated) and decay (system-initiated) must stay distinguishable
in the audit log — the SDK already makes this distinction for reconciliation vs.
forget, and decay is a third category.

Recent work explicitly on this axis:
[FSFM: biologically-inspired selective forgetting for agent memory](https://arxiv.org/pdf/2604.20300),
[Control-Plane Placement Shapes Forgetting](https://arxiv.org/pdf/2606.15903).

### 5.5 Procedural memory that earns its place

Our `procedural` type currently stores text. AWM
([arXiv:2409.07429](https://arxiv.org/abs/2409.07429)) and Voyager
([arXiv:2305.16291](https://arxiv.org/abs/2305.16291)) show what it should store:
**abstracted, verified, executable routines**, induced from successful trajectories
and indexed by natural-language description.

The design implication is a **success-gated write path**: a procedure is not
written on observation, it is written when a trajectory is *verified successful*,
and it carries a success-rate statistic that is updated on each reuse. This makes
procedural memory the one type with a closed feedback loop — and the one place
where reinforcement signal can enter the store.

### 5.6 Graph memory: entity resolution and community summaries

Two pieces, both offline:

**Entity resolution.** Without it, "Priyanshu", "P. Krishnan", and
`user_42` become three profiles and the graph fragments. The standard pipeline —
**blocking → pairwise scoring → clustering** — is the only way to avoid `O(N²)`
([Blocking & filtering survey, arXiv:1905.06167](https://arxiv.org/pdf/1905.06167)).
Recommended cascade: cheap blocking key (normalised name / embedding LSH bucket) →
similarity function → LLM adjudication only on the ambiguous band. Cost scales with
the ambiguous band, not with `N`.

**Community summarisation.** GraphRAG
([arXiv:2404.16130](https://arxiv.org/abs/2404.16130)) runs **Leiden**
([Traag, Waltman & van Eck, arXiv:1810.08473](https://arxiv.org/abs/1810.08473) —
guarantees well-connected communities, which Louvain does not) hierarchically over
the entity graph, then LLM-summarises each community. This is what makes *global*
questions ("what are the main themes with this user?") answerable at all — pure
top-`k` retrieval structurally cannot answer them.

**Graph-propagated retrieval.** HippoRAG's Personalized PageRank
([arXiv:2405.14831](https://arxiv.org/abs/2405.14831)): seed the restart
distribution with query-matched entity nodes, run PPR to convergence, rank memories
by accumulated node mass.

```
π = α·s + (1−α)·Pᵀπ        α ≈ 0.15,  ~20 power iterations
```

`O(iters · |E|)` per query, on a graph that is per-scope and therefore small.
Cheap enough to run inline; 10–30× cheaper and 6–13× faster than iterative
retrieval (IRCoT) at comparable multi-hop quality.

---

## Part VI — Governance, safety, privacy

### 6.1 The threat model is not hypothetical

Memory is a **persistence primitive for adversaries**. Three concrete attack
classes with published success rates:

| Attack | Mechanism | Reported efficacy |
|---|---|---|
| **AgentPoison** ([NeurIPS '24](https://proceedings.neurips.cc/paper_files/paper/2024/file/eb113910e9c3f6242541c1652e30dfd6-Paper-Conference.pdf)) | Optimised trigger injected into RAG KB / long-term memory | ≥80% ASR at <0.1% poison rate, ≤1% benign degradation |
| **MINJA** (memory injection) | *Query-level access only* — no direct store access needed | 98.2% injection success, 76.8% ASR |
| **MemoryGraft** ([arXiv:2512.16962](https://arxiv.org/html/2512.16962v1)) | Poisoned *experience* retrieval — persistent compromise | persistent across sessions |

The critical property: a poisoned memory **survives the session**, so a single
successful injection compromises all future interactions. This is categorically
worse than prompt injection.

### 6.2 Trust tiers and the promotion gate

The defence with the best structural fit — and the one this SDK is already
95% architected for — is to **separate untrusted candidate memory from trusted
typed memory, and require mediated promotion**:

```
 tier 0  QUARANTINE   ← anything derived from tool output, web content,
                        or other agents. Retrievable ONLY with explicit opt-in.
 tier 1  OBSERVED     ← derived from user conversation. Default write tier.
 tier 2  CONFIRMED    ← survived reconciliation / corroborated by ≥2 independent
                        sources / explicitly confirmed by the user.
 tier 3  ASSERTED     ← operator-provided ground truth. Never auto-superseded.
```

Promotion `0 → 1` requires a policy check; `1 → 2` requires corroboration; `2 → 3`
is human-only. **`MemoryOrigin` is already the substrate for this** — it records
exactly which write path produced a row. Trust tier is the missing orthogonal
column, and the retrieval plane gains a `min_trust` parameter.

Additional layered controls:

- **Provenance chain.** Every derived record links to sources via `memory_edges`;
  a poisoned source can be traced to every conclusion it contaminated and the whole
  subtree invalidated in one operation.
- **Write-time anomaly detection.** Poisoned memories are frequently embedding-space
  outliers (AgentPoison's trigger optimisation makes them cluster *tightly* — which
  is itself a detectable signature). Flag unusual-density writes for quarantine.
- **Tamper-evident audit.** Hash-chain the audit log (`h_n = H(h_{n-1} ‖ entry_n)`)
  so silent history edits are detectable.
- **Rate limits per origin.** A tool that writes 10k memories in a minute is an
  incident, not a feature.

Surveys: [Long-Term Memory Security in LLM Agents: Attacks, Defenses, and Governance](https://arxiv.org/abs/2604.16548),
[SSGM: Stability and Safety Governed Memory](https://arxiv.org/html/2603.11768v1),
[Memory Poisoning Attack and Defense](https://arxiv.org/abs/2601.05504).

### 6.3 Erasure that actually erases

`erase_all()` handles rows. It does not handle:

1. **Derived records.** A fact consolidated from a deleted turn still encodes it.
   The `memory_edges` graph makes cascading deletion tractable — and is the *only*
   thing that does.
2. **Index residue.** Tombstoned HNSW nodes remain traversable structures; graph
   neighbourhoods leak the deleted vector's position. Requires periodic rebuild,
   and the rebuild SLA must be documented, since GDPR Art. 17 has a deadline.
3. **Summaries and community reports.** Regenerate, don't patch.
4. **Information backflow** — recent work shows content removed from memory can be
   *regenerated from parametric residue* and written back, silently reversing the
   deletion ([Agentic Unlearning](https://arxiv.org/html/2602.17692v1)). Mitigation:
   a deny-list checked at the write path, not only at the read path.

Ship an **`ErasureCertificate`**: rows deleted, edges cascaded, indexes rebuilt (with
timestamps), summaries regenerated, deny-list entries created. Our existing
`ErasureReport` is the right seed.

Legal/technical framing:
[Algorithms that forget: machine unlearning and the right to erasure](https://www.sciencedirect.com/science/article/pii/S026736492300095X),
[Towards Probabilistic Verification of Machine Unlearning](https://arxiv.org/pdf/2003.04247).

### 6.4 Multi-tenant isolation

Already the SDK's strongest property (scope predicates enforced in SQL on every
read and write). Two residual gaps worth closing:

- **Index-level isolation.** A shared ANN index is a shared traversal structure.
  Per-scope-prefix partitioning (§3.1) closes this *and* is the right performance
  answer — a rare alignment.
- **Embedding-model isolation.** Two tenants on different embedding models must not
  share an index. Store `embedding_model_id` + `dim` per row and refuse
  cross-model comparison at the type level.

---

## Part VII — Evaluation

### 7.1 Benchmarks worth running

| Benchmark | Measures | Shape | Link |
|---|---|---|---|
| **LongMemEval** | extraction, cross-session reasoning, temporal reasoning, knowledge updates, **abstention** | 500 questions, ~40 sessions / ~115K tokens each | [arXiv:2410.10813](https://arxiv.org/abs/2410.10813) |
| **LoCoMo** | very-long-term conversational memory; QA, event summarisation, multi-modal dialogue | ~300 turns / ~9K tokens per dialogue, up to 35 sessions | [arXiv:2402.17753](https://arxiv.org/abs/2402.17753) |
| **HotpotQA / MuSiQue** | multi-hop retrieval in isolation | standard | — |
| **MSMARCO / BEIR** | retrieval-plane regression only | standard | — |

`benchmarks/quality/longmemeval_adapter.py` already exists — good. Two notes:

- **Abstention is the underrated slice.** A memory system that confidently answers
  from a superseded fact is worse than one that says "I don't know." Bi-temporal
  filtering (§5.3) should show up as an abstention win, and if it doesn't, the
  implementation is wrong.
- **Beware sycophancy.** [MemSyco-Bench](https://arxiv.org/pdf/2607.01071) shows
  memory systems can amplify agreement bias — the agent recalls what the user
  wants to hear. Worth a targeted eval given `entity_profiles` stores preferences.

### 7.2 The metrics that actually matter

Retrieval-plane (necessary, not sufficient): Recall@k, nDCG@k, MRR — already in
`benchmarks/quality/ir_metrics.py`.

**System-level, and this is the real scorecard:**

| Metric | Definition | Why |
|---|---|---|
| **Accuracy per 1K context tokens** | task accuracy ÷ (assembled tokens / 1000) | The actual objective function (§1.3) |
| **Update latency** | turns between a fact changing and retrieval reflecting it | Measures the evolution plane |
| **Contradiction rate** | % of assembled cards containing mutually inconsistent facts | Measures reconciliation |
| **Staleness rate** | % of answers grounded in an invalidated fact | Measures bi-temporal correctness |
| **Poisoning resistance** | ASR under AgentPoison-style injection at fixed poison rate | Measures the governance plane |
| **Cost per session** | embedding + LLM + storage, amortised | Mem0's headline claim is a cost claim |

### 7.3 Ablation discipline

The literature contains an uncomfortable result worth taking seriously:
[*Verbatim Chunks Beat Extracted Artifacts*](https://arxiv.org/pdf/2601.00821)
finds that in a controlled ablation, storing raw conversation chunks outperformed
LLM-extracted facts for long-conversation memory. Consolidation is not free and is
not obviously net-positive.

> **Design position.** Every component in Part III and Part V must be
> independently ablatable via config, and the benchmark harness must report the
> full ablation grid. `benchmarks/quality/test_config_matrix.py` is the right
> place. **Ship nothing that doesn't win its own ablation.**

Related: [Entity-Collision: A Stratified Protocol for Attributing Retrieval Lift in Agent Memory](https://arxiv.org/pdf/2605.29630)
— a protocol for determining whether a reported lift comes from the mechanism or
from entity-overlap leakage. Directly applicable to how we report our own numbers.

---

## Part VIII — Migration path

Ordered by `(leverage ÷ cost)`, with dependencies respected.

### Phase 1 — Foundations (no new dependencies, no breaking changes)

| # | Change | Where | Why |
|---|---|---|---|
| 1.1 | **`memory_edges` table** (`src`, `dst`, `type`, `weight`) | new migration | Unlocks provenance, cascading erasure, reflection, graph retrieval, community summaries. Everything downstream needs it. |
| 1.2 | **Bi-temporal columns** `valid_from` / `valid_to` on facts + profiles | migration + `Reconciler` | Correctness fix for temporal queries; backwards compatible |
| 1.3 | **MMR diversification** in `search()` | `repositories/base.py` | ~40 LOC, large context-card quality win |
| 1.4 | **Trust tier column** + `min_trust` read param | models + repos | Poisoning defence with the best structural fit |
| 1.5 | **Weighted RRF** (expose `w_i`, `K`) | `_rrf_fuse` | Already 90% there |
| 1.6 | **Scoring component logging** | `search()` | Prerequisite for fitting weights instead of guessing |

### Phase 2 — Retrieval quality

| # | Change | Why |
|---|---|---|
| 2.1 | **Contextual Retrieval** — `context_prefix` on chunks, embed prefix+content | Best measured ROI in the document (−49% retrieval failure) |
| 2.2 | **Real BM25** (Db2 text index or TF side-table) replacing the Python overlap ranker | Fixes the proper-noun/ID failure mode |
| 2.3 | **Matryoshka two-stage** (`embedding_short` + `embedding_full`) | 4–12× index memory reduction, ~free |
| 2.4 | **Budget-aware packing + U-curve ordering** in `get_context_card()` | Directly targets the §1.3 objective |
| 2.5 | **Query understanding**: temporal extraction, intent routing | Makes 1.2 actually reachable |

### Phase 3 — Evolution plane

| # | Change | Why |
|---|---|---|
| 3.1 | **Durable job queue** (`memory_jobs`, `SKIP LOCKED`) — promote background worker to primary | Sleep-time compute; unblocks 3.2–3.5 |
| 3.2 | **Reflection pass** writing derived records with `DERIVED_FROM` edges | Generative Agents' highest-value mechanism |
| 3.3 | **DSR decay + hot/warm/cold eviction ladder** | Unbounded growth is the #1 production failure mode |
| 3.4 | **Entity resolution** (blocking → score → LLM adjudication) | Without it the graph fragments and profiles multiply |
| 3.5 | **A-MEM-style evolution** — new writes update neighbour tags/links | Self-refining network |

### Phase 4 — Reasoning plane

| # | Change | Why |
|---|---|---|
| 4.1 | **Entity graph** projection over `memory_edges` | Substrate for 4.2–4.3 |
| 4.2 | **Personalized PageRank** retriever as an RRF arm | Multi-hop, cheap, well-validated |
| 4.3 | **Leiden hierarchical community summaries** | Only way to answer global/thematic questions |
| 4.4 | **Verified procedural memory** with success-rate stats | Closes the reinforcement loop |

### Phase 5 — Scale and hardening

| # | Change | Why |
|---|---|---|
| 5.1 | ~~`VectorIndex` protocol~~ → **per-scope-prefix index partitioning** (spike first) | Turns hard filtered-ANN into easy unfiltered ANN; also closes the index-isolation gap. The protocol half is **retracted** — see the correction in §3.1. Tracked as VIDX-7. |
| 5.2 | ~~DiskANN / RaBitQ backends~~ → **enumerate what `CREATE VECTOR INDEX` actually exposes** | Retracted: Db2's index is already DiskANN and its internals are not ours to swap. The real work is documenting the tuning surface we do have. Tracked as VIDX-2. |
| 5.3 | **Hash-chained audit log + `ErasureCertificate`** | Compliance-grade erasure story |
| 5.4 | **Poisoning eval in CI** (AgentPoison-style ASR regression) | Security as a benchmark, not a claim |
| 5.5 | **Cross-encoder rerank adapter** (optional extra) | Quality ceiling for high-stakes reads |

### What deliberately stays out

- **ColBERT/PLAID in the default path** — storage cost per token is not justified
  until the ablation demands it. Keep as an optional adapter, listed as the known
  ceiling.
- **CRDTs on content** — wrong semantics for facts; reconciliation is the correct
  mechanism (§3.8).
- **Parametric memory / fine-tuning** — a different product with a different
  governance story. Titans ([arXiv:2501.00663](https://arxiv.org/abs/2501.00663))
  is worth tracking as the strongest work on test-time-learned memory, but it is a
  model-architecture concern, not an SDK concern.
- **Hand-rolled interval trees** — Db2 temporal tables do this better (§3.6).

---

## Appendix A — Consolidated bibliography

### Surveys and frameworks

- [Memory in the Age of AI Agents](https://arxiv.org/abs/2512.13564) — arXiv:2512.13564
- [Agent-Memory-Paper-List](https://github.com/Shichun-Liu/Agent-Memory-Paper-List) — companion bibliography
- [Cognitive Architectures for Language Agents (CoALA)](https://arxiv.org/abs/2309.02427) — arXiv:2309.02427
- [LLM Agent Memory: A Unified Representation–Management Survey](https://openreview.net/forum?id=KPs1EgGKcT)
- [Always-On Agents: Persistent Memory, State, and Governance](https://arxiv.org/pdf/2606.30306)
- [Memory for Autonomous LLM Agents: Mechanisms, Evaluation, Frontiers](https://arxiv.org/html/2603.07670v1)
- [Adaptation of Agentic AI: Post-Training, Memory, and Skills](https://arxiv.org/pdf/2512.16301)

### Memory architectures

- [MemGPT: Towards LLMs as Operating Systems](https://arxiv.org/abs/2310.08560) — arXiv:2310.08560
- [Generative Agents: Interactive Simulacra of Human Behavior](https://dl.acm.org/doi/fullHtml/10.1145/3586183.3606763) — UIST '23
- [HippoRAG (NeurIPS '24)](https://arxiv.org/abs/2405.14831) · [HippoRAG 2: From RAG to Memory](https://arxiv.org/pdf/2502.14802)
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory](https://arxiv.org/abs/2501.13956) — arXiv:2501.13956
- [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110) — arXiv:2502.12110
- [Mem0: Production-Ready AI Agents with Scalable Long-Term Memory](https://arxiv.org/abs/2504.19413) — arXiv:2504.19413
- [Sleep-time Compute: Beyond Inference Scaling at Test-time](https://arxiv.org/abs/2504.13171) — arXiv:2504.13171
- [Agent Workflow Memory](https://arxiv.org/abs/2409.07429) — arXiv:2409.07429, ICML '25
- [Voyager: An Open-Ended Embodied Agent with LLMs](https://arxiv.org/abs/2305.16291) — arXiv:2305.16291
- [Titans: Learning to Memorize at Test Time](https://arxiv.org/abs/2501.00663) — arXiv:2501.00663

### Indexes, quantization, filtered ANN

- [HNSW (Malkov & Yashunin)](https://arxiv.org/abs/1603.09320) — arXiv:1603.09320
- [DiskANN (NeurIPS '19)](https://www.microsoft.com/en-us/research/publication/diskann-fast-accurate-billion-point-nearest-neighbor-search-on-a-single-node/)
- [Filtered-DiskANN (WWW '23)](https://harsha-simhadri.org/pubs/Filtered-DiskANN23.pdf)
- [ACORN (SIGMOD '24)](https://dl.acm.org/doi/10.1145/3654923)
- [RaBitQ (SIGMOD '24)](https://arxiv.org/abs/2405.12497) — arXiv:2405.12497
- [Filtered ANN: unified benchmark & experimental study](https://arxiv.org/html/2509.07789v1)
- [VIBE: Vector Index Benchmark for Embeddings](https://arxiv.org/pdf/2505.17810)

### Retrieval and ranking

- [Reciprocal Rank Fusion (SIGIR '09)](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf)
- [MMR (Carbonell & Goldstein, SIGIR '98)](https://www.cs.cmu.edu/~jgc/publication/The_Use_MMR_Diversity_Based_LTMIR_1998.pdf)
- [ColBERTv2](https://arxiv.org/abs/2112.01488) · [PLAID](https://arxiv.org/pdf/2205.09707)
- [Matryoshka Representation Learning (NeurIPS '22)](https://proceedings.neurips.cc/paper_files/paper/2022/file/c32319f4868da7613d78af9993100e42-Paper-Conference.pdf)
- [HyDE: Precise Zero-Shot Dense Retrieval](https://arxiv.org/abs/2212.10496)
- [Anthropic — Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
- [Lost in the Middle](https://arxiv.org/abs/2307.03172) · [Context Rot (Chroma)](https://research.trychroma.com/context-rot)

### Graph, temporal, and entity resolution

- [GraphRAG: From Local to Global](https://arxiv.org/abs/2404.16130) — arXiv:2404.16130
- [Leiden: guaranteeing well-connected communities](https://arxiv.org/abs/1810.08473)
- [TSQL2 data model (Jensen, Snodgrass, Soo)](https://people.cs.aau.dk/~csj/Thesis/pdf/chapter12.pdf)
- [Bitemporal History (Fowler)](https://martinfowler.com/articles/bitemporal-history.html)
- [Bitemporal databases: PRISMA systematic review](https://link.springer.com/article/10.1007/s42488-026-00162-x)
- [Survey of Blocking and Filtering for Entity Resolution](https://arxiv.org/pdf/1905.06167)
- [ATOM: adaptive dynamic temporal KG construction with LLMs](https://arxiv.org/pdf/2510.22590)

### Forgetting and decay

- [FSRS algorithm (DSR model, power-law retrievability)](https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm)
- [MaiMemo — Optimizing Spaced Repetition Schedule by Capturing the Dynamics of Memory (IEEE TKDE)](http://www.maimemo.com/paper/) — the peer-reviewed DSR foundation
- [History of spaced repetition algorithms](https://expertium.github.io/History.html)
- [Unbounded Human Learning: Optimal Scheduling for Spaced Repetition](https://arxiv.org/pdf/1602.07032)
- [FSFM: Biologically-Inspired Selective Forgetting for Agent Memory](https://arxiv.org/pdf/2604.20300)
- [Control-Plane Placement Shapes Forgetting](https://arxiv.org/pdf/2606.15903)

### Security, privacy, governance

- [AgentPoison (NeurIPS '24)](https://proceedings.neurips.cc/paper_files/paper/2024/file/eb113910e9c3f6242541c1652e30dfd6-Paper-Conference.pdf)
- [Memory Poisoning Attack and Defense on Memory-Based LLM Agents](https://arxiv.org/abs/2601.05504)
- [MemoryGraft: Persistent Compromise via Poisoned Experience Retrieval](https://arxiv.org/html/2512.16962v1)
- [Long-Term Memory Security in LLM Agents: Attacks, Defenses, Governance](https://arxiv.org/abs/2604.16548)
- [SSGM: Stability and Safety Governed Memory](https://arxiv.org/html/2603.11768v1)
- [Hijacking Agent Memory: Stealthy Trojan Attacks](https://arxiv.org/html/2605.29960)
- [Agentic Unlearning: When LLM Agent Meets Machine Unlearning](https://arxiv.org/html/2602.17692v1)
- [Algorithms that forget: machine unlearning and the right to erasure](https://www.sciencedirect.com/science/article/pii/S026736492300095X)
- [Towards Probabilistic Verification of Machine Unlearning](https://arxiv.org/pdf/2003.04247)

### Evaluation

- [LongMemEval](https://arxiv.org/abs/2410.10813) — arXiv:2410.10813
- [LoCoMo: Evaluating Very Long-Term Conversational Memory](https://arxiv.org/abs/2402.17753)
- [Verbatim Chunks Beat Extracted Artifacts](https://arxiv.org/pdf/2601.00821) — the ablation that should keep us honest
- [Entity-Collision: Attributing Retrieval Lift in Agent Memory](https://arxiv.org/pdf/2605.29630)
- [MemSyco-Bench: Benchmarking Sycophancy in Agent Memory](https://arxiv.org/pdf/2607.01071)
- [EvoMemBench: Agent Memory from a Self-Evolving Perspective](https://arxiv.org/pdf/2605.18421)

### Classical CS foundations

- [Count–Min Sketch (Cormode & Muthukrishnan, J. Algorithms 2005)](https://doi.org/10.1016/j.jalgor.2003.12.001)
- [HyperLogLog (Flajolet, Fusy, Gandouet & Meunier, DMTCS 2007)](https://dmtcs.episciences.org/3545)
- [Conflict-Free Replicated Data Types (Shapiro et al., SSS 2011)](https://link.springer.com/chapter/10.1007/978-3-642-24550-3_29)
- [Nemhauser, Wolsey & Fisher — submodular greedy (1−1/e) bound](https://link.springer.com/article/10.1007/BF01588971)

---

## Appendix B — Open questions

1. **Does consolidation beat verbatim storage in our setting?** The literature is
   genuinely split. Run the ablation before Phase 3.2 ships.
2. **How much does Db2-native vector search cost us vs. a specialised index?**
   Unknown until 5.1 gives us a comparison point. The governance argument for Db2
   is strong, but it should be a measured trade, not an assumed one.
3. **What is the right decay half-life per memory type?** `working` should decay in
   hours; `procedural` should arguably never decay while its success rate holds.
   Fit against LongMemEval's knowledge-update slice.
4. **Can trust-tier promotion be automated safely?** The `1 → 2` corroboration rule
   is the risky one — an attacker who can write twice can self-corroborate.
   Independence of sources needs a real definition.
5. **Does per-scope index partitioning create a long-tail problem?** Thousands of
   tiny indexes have their own pathologies. Threshold-based: partition above a
   cardinality floor, share below it.

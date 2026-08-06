---
title: Software Design Documentation — Package Index
owner: agent-memory-sdk
date: 2026-08-09
epic: EPIC-9
document-id: SDD-12
---

# Software Design Documentation — Package Index

This directory contains the point-in-time Software Design Documentation (SDD)
package for the `agent-memory-sdk`, produced as EPIC-9.  It is a structured
design-review artifact, not a living document — later code changes may diverge
from details here without this package being updated.  For the current-state
view of any topic, consult the living documents listed in
[Relationship to other project docs](#relationship-to-other-project-docs).

---

## Documents in reading order

### [SDD-1 — System Architecture](01-system-architecture.md)

High-level overview of the SDK's layered architecture: the public facade
(`MemoryStore`), the five per-type repositories, the shared `BaseRepository`
abstraction, the plugin protocol chain (`EmbeddingProvider`, `Consolidator`,
`Reconciler`, `IngestResolver`, `MemoryExtractor`, `IntegrityGuard`), and the
Db2 persistence layer.  Start here to understand how the pieces fit together
before reading deeper documents.

### [SDD-2 — Data Architecture](02-data-architecture.md)

Schema design for all six Db2 tables (`working_memory`, `episodic_memory`,
`semantic_facts`, `entity_profiles`, `procedural_memory`, `memory_chunks`),
the shared column set (`_SELECT_COLS`), migration strategy, vector storage
conventions, the content-hash dedup mechanism, and the soft-delete / soft-
supersession audit columns.  Read after SDD-1 if you need to understand the
physical data model.

### [SDD-3 — API and Interface Specification](03-api-interface-spec.md)

Public surface of `MemoryStore` and the five repositories: method signatures,
parameter contracts, return types, exception taxonomy, and the seven injected
protocol interfaces.  The canonical reference for calling the SDK correctly.

### [SDD-4 — Sequence Flows](04-sequence-flows.md)

Step-by-step trace of the four primary runtime flows: `remember()` with the
full plugin chain (IngestResolver → IntegrityGuard → create → Consolidator),
`add_messages()` with MemoryExtractor, `get_context_card()` with long-term
blending, and `reconcile()`.  Use these diagrams when debugging unexpected
behavior or designing new plugins.

### [SDD-5 — Security Design](05-security-design.md)

Threat model, trust boundaries, the multi-tenant scope-predicate enforcement
mechanism, SQL-injection prevention via vector-string coercion, metadata-filter
schema policy, soft-delete audit trail, and the TRU-2 `IntegrityGuard` write-
time anomaly detection extension point.  Read before writing any code that
touches authentication, tenancy, or adversarial inputs.

### [SDD-6 — Data Governance](06-data-governance.md)

Lifecycle governance: right-to-erasure (`erase_all()`, `ErasureReport`),
soft-delete (`forget()`), soft-supersession (`reconcile()`), TTL expiry,
the `MemoryOrigin` provenance enum (TRU-1), the `quarantined` flag (TRU-2),
and the audit-column separation rationale (`deleted_at` vs `superseded_at`).

### [SDD-7 — Extensibility Architecture](07-extensibility-architecture.md)

How the seven protocol interfaces (`EmbeddingProvider`, `Consolidator`,
`Reconciler`, `IngestResolver`, `MemoryExtractor`, `Summarizer`,
`IntegrityGuard`) are composed, the `NoOp*` default chain, the `Thread`
convenience facade, and guidance for extending the SDK with new memory types
or custom plugins.

### [SDD-8 — NFR: Performance and Capacity](08-nfr-performance-capacity.md)

Non-functional requirements for write throughput, search latency, vector
dimension budget, chunking threshold, the ANN index (`WITH DISTANCE COSINE`)
and its RUNSTATS dependency, `SearchMode` (APPROX vs EXACT), and capacity
sizing guidance for the five memory tables.

### [SDD-9 — Deployment and Operations](09-deployment-operations.md)

Installation, `ConnectionPool` configuration, migration execution
(`db.migrate`), environment variable reference, pool sizing, health-check
patterns, and the `purge_expired()` / `erase_all()` maintenance entry points.

### [SDD-10 — Testing and QA Strategy](10-testing-qa-strategy.md)

Test pyramid: unit (fake-pool, row-tuple helpers), integration (live Db2, skip-
guarded), retrieval-quality benchmark suite (LongMemEval-shaped, OllamaJudge,
Tier 2 / nightly-only), and the isolation/load suite (Locust, BM-13).  CI
strategy, coverage thresholds, and the LLM-judged-suite-never-a-PR-gate rule.

### [SDD-11 — Risk Register](11-risk-register.md)

Identified technical risks with likelihood/impact ratings and mitigations:
Db2 driver version sensitivity, vector-index RUNSTATS requirement, LLM-judge
non-determinism, consolidator hot-path blocking, adversarial memory poisoning
(FARMA/SENTINEL), and migration rollback complexity.

---

## Relationship to other project docs

This SDD package is a **point-in-time structured design-review artifact**
produced at a defined milestone (EPIC-9, 2026-08-09).  It is not a living
document and does not supersede any of the following:

| Document | What it is | Relationship to this package |
|---|---|---|
| `ARCHITECTURE.md` (repo root) | Living current-state summary — updated as the code evolves | The canonical "what is true now" reference; this SDD captures the design intent at EPIC-9 freeze |
| `project-management/DECISIONS.md` | Chronological append-only decision log | Records the *why* behind choices; the SDD records the *what* without repeating every rationale |
| `project-management/BENCHMARKS.md` | Living benchmark results data, updated after each run | Contains measured performance numbers; the SDD's [SDD-8](08-nfr-performance-capacity.md) states targets, not actuals |

When in doubt about what is currently true in the codebase, read
`ARCHITECTURE.md` and the source.  When in doubt about why a decision was
made, read `DECISIONS.md`.  This package answers "what was the design intent
and rationale at the EPIC-9 milestone."

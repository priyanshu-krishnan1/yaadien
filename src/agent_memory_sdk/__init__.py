"""
agent-memory-sdk
~~~~~~~~~~~~~~~~
Governed multi-type memory system for AI agents backed by IBM Db2 LUW.

Memory types:
  - working   : raw current-session/thread turns (short-lived)
  - episodic  : summarized past runs/threads/events
  - semantic  : extracted facts + aggregated entity/user profiles
  - procedural: learned skills/instructions/how-to knowledge

Quick start::

    from agent_memory_sdk.db.connection import ConnectionPool
    from agent_memory_sdk import MemoryStore, MemoryScope, WorkingMemory

    pool = ConnectionPool()          # reads DB2_* env vars automatically
    store = MemoryStore(pool)
    scope = MemoryScope(agent_id="agent-001")

    record = store.remember(
        WorkingMemory(agent_id="agent-001", content="Hello!"),
        scope,
    )
    store.forget(record.id, "working", scope)
    store.purge_expired(scope)
"""

from agent_memory_sdk.db.migrate import SchemaPolicy
from agent_memory_sdk.exceptions import (
    InvalidMetadataFilterError,
    SchemaPolicyError,
    ScopeMismatchError,
    StaleWriteError,
)
from agent_memory_sdk.models import (
    EntityProfile,
    EpisodicMemory,
    MemoryScope,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import (
    ContextCard,
    DistanceMetric,
    EmbeddingProvider,
    ErasureReport,
    NoOpConsolidator,
    NoOpReconciler,
    NoOpSummarizer,
    Reconciler,
    SearchMode,
    Summarizer,
    SupersedeDecision,
)

__version__ = "0.1.0"

__all__ = [
    "MemoryStore",
    "MemoryScope",
    "WorkingMemory",
    "EpisodicMemory",
    "SemanticFact",
    "EntityProfile",
    "ProceduralMemory",
    "EmbeddingProvider",
    "DistanceMetric",
    "SearchMode",
    "NoOpConsolidator",
    "NoOpReconciler",
    "Reconciler",
    "SupersedeDecision",
    "StaleWriteError",
    "InvalidMetadataFilterError",
    "ScopeMismatchError",
    "SchemaPolicyError",
    "SchemaPolicy",
    "ContextCard",
    "Summarizer",
    "NoOpSummarizer",
    "ErasureReport",
]

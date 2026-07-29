"""
store.py
~~~~~~~~
MemoryStore — the top-level facade that composes all five repositories.

Callers normally import and use only this class::

    from agent_memory_sdk.store import MemoryStore
    from agent_memory_sdk.models import MemoryScope, WorkingMemory

    store = MemoryStore(pool)

    scope = MemoryScope(agent_id="agent-001", user_id="user-42")

    # Write
    record = store.working.create(
        WorkingMemory(agent_id=scope.agent_id, content="Hello!"),
        scope,
    )

    # Read back
    found = store.working.get_by_id(record.id, scope)

    # Semantic search
    results = store.working.search(
        query_embedding=[0.1, 0.2, ...],
        scope=scope,
        top_k=5,
    )

The ``MemoryStore`` itself adds no business logic on top of the
repositories in Step 3; it is a composition root only.  Lifecycle logic
(TTL, soft-delete via ``forget()``, consolidation callbacks) will be
added in Step 4.
"""

from __future__ import annotations

from typing import Any

from agent_memory_sdk.repositories.episodic import EpisodicMemoryRepository
from agent_memory_sdk.repositories.facts import SemanticFactRepository
from agent_memory_sdk.repositories.procedural import ProceduralMemoryRepository
from agent_memory_sdk.repositories.profiles import EntityProfileRepository
from agent_memory_sdk.repositories.working import WorkingMemoryRepository


class MemoryStore:
    """Composition root for all five memory-type repositories.

    Attributes:
        working:    :class:`~agent_memory_sdk.repositories.WorkingMemoryRepository`
        episodic:   :class:`~agent_memory_sdk.repositories.EpisodicMemoryRepository`
        facts:      :class:`~agent_memory_sdk.repositories.SemanticFactRepository`
        profiles:   :class:`~agent_memory_sdk.repositories.EntityProfileRepository`
        procedures: :class:`~agent_memory_sdk.repositories.ProceduralMemoryRepository`

    Args:
        pool: A :class:`~agent_memory_sdk.db.connection.ConnectionPool`
              instance, or any object whose ``get_connection()`` context
              manager yields a DB-API 2.0 connection.
        embedding_dim: The vector dimension used by all tables (default
              1536, matching the DDL default in 0002_memory_tables.sql).
              Override if you change the schema to a different model.
    """

    def __init__(
        self,
        pool: Any,
        embedding_dim: int = 1536,
    ) -> None:
        self.working = WorkingMemoryRepository(pool)
        self.episodic = EpisodicMemoryRepository(pool)
        self.facts = SemanticFactRepository(pool)
        self.profiles = EntityProfileRepository(pool)
        self.procedures = ProceduralMemoryRepository(pool)

        # Propagate the embedding dimension to all repos so they can
        # produce correctly-dimensioned zero-vector sentinels.
        for repo in (
            self.working,
            self.episodic,
            self.facts,
            self.profiles,
            self.procedures,
        ):
            repo.EMBEDDING_DIM = embedding_dim

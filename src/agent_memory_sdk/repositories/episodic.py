"""
repositories/episodic.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repository for the ``episodic_memory`` table.

Episodic memory stores summarized records of past runs/threads/events.
Produced by the Consolidator after a working-memory session concludes.
"""

from __future__ import annotations

import json
from typing import Any

from agent_memory_sdk.models import EpisodicMemory
from agent_memory_sdk.repositories.base import BaseRepository, _parse_dt, _parse_vector


class EpisodicMemoryRepository(BaseRepository[EpisodicMemory]):
    """Repository for ``episodic_memory``.

    ``_HAS_CONSOLIDATED_AT = True`` because migration 0005 adds a
    ``consolidated_at`` TIMESTAMP column to this table (ENH-4).  The claim-
    based locking helper ``_claim_consolidated()`` is available on this
    repository.
    """

    _TABLE = "episodic_memory"
    _MODEL = EpisodicMemory
    _HAS_CONSOLIDATED_AT = True

    # Extend base SELECT list with consolidated_at (index 15).
    _SELECT_COLS = (
        "id, tenant_id, agent_id, user_id, thread_id, "
        "content, metadata, "
        "VECTOR_SERIALIZE(embedding) AS embedding, "
        "confidence, content_hash, "
        "created_at, updated_at, expires_at, version, deleted_at, "
        "consolidated_at"
    )

    def _model_from_row(self, row: tuple[Any, ...]) -> EpisodicMemory:
        """Map a DB-API result row to an :class:`EpisodicMemory` instance.

        Column order must match ``_SELECT_COLS`` above:
          0  id
          1  tenant_id
          2  agent_id
          3  user_id
          4  thread_id
          5  content
          6  metadata        (JSON string)
          7  embedding       (serialized vector string from VECTOR_SERIALIZE)
          8  confidence
          9  content_hash    (hex SHA-256, or None for pre-migration rows)
          10 created_at
          11 updated_at
          12 expires_at
          13 version
          14 deleted_at
          15 consolidated_at (None = not yet consolidated; ENH-4 / migration 0005)
        """
        (
            id_, tenant_id, agent_id, user_id, thread_id,
            content, metadata_str,
            embedding_str,
            confidence,
            content_hash,
            created_at, updated_at, expires_at, version, deleted_at,
            consolidated_at,
        ) = row

        return EpisodicMemory(
            id=id_,
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            thread_id=thread_id,
            content=content,
            metadata=json.loads(metadata_str) if metadata_str else {},
            embedding=_parse_vector(embedding_str),
            confidence=float(confidence) if confidence is not None else 1.0,
            content_hash=content_hash,
            created_at=_parse_dt(created_at),
            updated_at=_parse_dt(updated_at),
            expires_at=_parse_dt(expires_at),
            version=version if version is not None else 1,
            deleted_at=_parse_dt(deleted_at),
            consolidated_at=_parse_dt(consolidated_at),
        )

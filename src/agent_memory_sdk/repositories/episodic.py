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
    """Repository for ``episodic_memory``."""

    _TABLE = "episodic_memory"
    _MODEL = EpisodicMemory

    def _model_from_row(self, row: tuple[Any, ...]) -> EpisodicMemory:
        (
            id_, tenant_id, agent_id, user_id, thread_id,
            content, metadata_str,
            embedding_str,
            confidence,
            created_at, updated_at, expires_at, version, deleted_at,
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
            created_at=_parse_dt(created_at),
            updated_at=_parse_dt(updated_at),
            expires_at=_parse_dt(expires_at),
            version=version if version is not None else 1,
            deleted_at=_parse_dt(deleted_at),
        )

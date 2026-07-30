"""
repositories/procedural.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Repository for the ``procedural_memory`` table.

Procedural memory stores learned skills, instruction sets, and how-to
knowledge.  Typically agent-scoped (user_id / thread_id are often None).
"""

from __future__ import annotations

import json
from typing import Any

from agent_memory_sdk.models import ProceduralMemory
from agent_memory_sdk.repositories.base import BaseRepository, _parse_dt, _parse_vector


class ProceduralMemoryRepository(BaseRepository[ProceduralMemory]):
    """Repository for ``procedural_memory``."""

    _TABLE = "procedural_memory"
    _MODEL = ProceduralMemory

    def _model_from_row(self, row: tuple[Any, ...]) -> ProceduralMemory:
        (
            id_, tenant_id, agent_id, user_id, thread_id,
            content, metadata_str,
            embedding_str,
            confidence,
            created_at, updated_at, expires_at, version, deleted_at,
        ) = row

        return ProceduralMemory(
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

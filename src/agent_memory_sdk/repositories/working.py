"""
repositories/working.py
~~~~~~~~~~~~~~~~~~~~~~~
Repository for the ``working_memory`` table.

Working memory stores raw current-session/thread turns.  It is typically
short-lived (expires_at set at write time by the caller) and has the
highest write rate of all memory types.
"""

from __future__ import annotations

import json
from typing import Any

from agent_memory_sdk.models import WorkingMemory
from agent_memory_sdk.repositories.base import BaseRepository, _parse_dt, _parse_vector


class WorkingMemoryRepository(BaseRepository[WorkingMemory]):
    """Repository for ``working_memory``."""

    _TABLE = "working_memory"
    _MODEL = WorkingMemory

    def _model_from_row(self, row: tuple[Any, ...]) -> WorkingMemory:
        """Map a DB-API result row to a :class:`WorkingMemory` instance.

        Column order must match ``_SELECT_COLS`` in BaseRepository:
          0  id
          1  tenant_id
          2  agent_id
          3  user_id
          4  thread_id
          5  content
          6  metadata      (JSON string)
          7  embedding     (serialized vector string from VECTOR_SERIALIZE)
          8  confidence
          9  created_at
          10 updated_at
          11 expires_at
          12 version
          13 deleted_at
        """
        (
            id_, tenant_id, agent_id, user_id, thread_id,
            content, metadata_str,
            embedding_str,
            confidence,
            created_at, updated_at, expires_at, version, deleted_at,
        ) = row

        return WorkingMemory(
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



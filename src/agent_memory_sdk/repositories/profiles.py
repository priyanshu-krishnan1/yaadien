"""
repositories/profiles.py
~~~~~~~~~~~~~~~~~~~~~~~~
Repository for the ``entity_profiles`` table.

Entity profiles store aggregated dense summaries of users or other entities.
Typically one row per (agent_id, user_id) pair; updated by the Consolidator.
"""

from __future__ import annotations

import json
from typing import Any

from agent_memory_sdk.models import EntityProfile
from agent_memory_sdk.repositories.base import BaseRepository, _parse_dt, _parse_vector


class EntityProfileRepository(BaseRepository[EntityProfile]):
    """Repository for ``entity_profiles``."""

    _TABLE = "entity_profiles"
    _MODEL = EntityProfile

    def _model_from_row(self, row: tuple[Any, ...]) -> EntityProfile:
        (
            id_, tenant_id, agent_id, user_id, thread_id,
            content, metadata_str,
            embedding_str,
            created_at, updated_at, expires_at, version, deleted_at,
        ) = row

        return EntityProfile(
            id=id_,
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_id=user_id,
            thread_id=thread_id,
            content=content,
            metadata=json.loads(metadata_str) if metadata_str else {},
            embedding=_parse_vector(embedding_str),
            created_at=_parse_dt(created_at),
            updated_at=_parse_dt(updated_at),
            expires_at=_parse_dt(expires_at),
            version=version if version is not None else 1,
            deleted_at=_parse_dt(deleted_at),
        )

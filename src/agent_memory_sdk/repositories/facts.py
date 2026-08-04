"""
repositories/facts.py
~~~~~~~~~~~~~~~~~~~~~
Repository for the ``semantic_facts`` table.

Semantic facts store atomic extracted facts about entities or the world
(e.g. "User prefers Python over Java.").  Created by the Consolidator.

ENH-3 additions
---------------
- ``supersede(loser_id, winner_id, reason, scope)`` — soft-supersede a row
  by setting its ``superseded_by``, ``superseded_at``, and
  ``supersede_reason`` columns.  Does NOT touch ``deleted_at`` — the two
  mechanisms are deliberately kept separate for audit purposes.
- ``_SELECT_COLS`` extended with the three supersession columns (indexes
  15, 16, 17 in the unpacked row tuple).
- ``list_all()`` and ``search()`` in base.py already exclude
  ``superseded_at IS NOT NULL`` rows; this repository inherits that
  filtering without any extra code here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from agent_memory_sdk.models import MemoryScope, SemanticFact
from agent_memory_sdk.repositories.base import (
    BaseRepository,
    _parse_dt,
    _parse_vector,
    _require_agent_id,
    _scope_predicates,
    logger,
)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class SemanticFactRepository(BaseRepository[SemanticFact]):
    """Repository for ``semantic_facts``.

    Extends the base repository with supersession support (ENH-3).
    """

    _TABLE = "semantic_facts"
    _MODEL = SemanticFact

    # This table has the supersession columns added by migration 0004.
    # Setting _HAS_SUPERSESSION = True causes BaseRepository.list_all(),
    # search(), and create()'s dedup-check to append "AND superseded_at IS NULL".
    _HAS_SUPERSESSION = True

    # Override _SELECT_COLS to include the three supersession columns.
    # Index map (0-based):
    #   0  id          1  tenant_id   2  agent_id    3  user_id     4  thread_id
    #   5  content     6  metadata    7  embedding
    #   8  confidence  9  content_hash
    #   10 created_at  11 updated_at  12 expires_at  13 version     14 deleted_at
    #   15 superseded_by  16 superseded_at  17 supersede_reason
    _SELECT_COLS = (
        "id, tenant_id, agent_id, user_id, thread_id, "
        "content, metadata, "
        "VECTOR_SERIALIZE(embedding) AS embedding, "
        "confidence, content_hash, "
        "created_at, updated_at, expires_at, version, deleted_at, "
        "superseded_by, superseded_at, supersede_reason"
    )

    # Plain alias list for the outer SELECT of the ROW_NUMBER pagination
    # subquery (see BaseRepository._SELECT_OUTER_COLS).
    _SELECT_OUTER_COLS = (
        "id, tenant_id, agent_id, user_id, thread_id, "
        "content, metadata, "
        "embedding, "
        "confidence, content_hash, "
        "created_at, updated_at, expires_at, version, deleted_at, "
        "superseded_by, superseded_at, supersede_reason"
    )

    def _model_from_row(self, row: tuple[Any, ...]) -> SemanticFact:
        (
            id_, tenant_id, agent_id, user_id, thread_id,
            content, metadata_str,
            embedding_str,
            confidence,
            content_hash,
            created_at, updated_at, expires_at, version, deleted_at,
            superseded_by, superseded_at, supersede_reason,
        ) = row

        return SemanticFact(
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
            superseded_by=superseded_by,
            superseded_at=_parse_dt(superseded_at),
            supersede_reason=supersede_reason,
        )

    def supersede(
        self,
        loser_id: str,
        winner_id: str,
        reason: str,
        scope: MemoryScope,
    ) -> bool:
        """Soft-supersede a fact row.

        Sets ``superseded_by``, ``superseded_at``, and ``supersede_reason``
        on the loser row.  The row remains in the table for audit purposes
        and is excluded from future :meth:`list_all` / :meth:`search` results
        because those methods filter on ``superseded_at IS NULL``.

        This is **not** a tombstone (``deleted_at`` is untouched) and it is
        **not** a hard delete.  The governance distinction:

        * ``deleted_at IS NOT NULL``  → user/operator asked us to forget this.
        * ``superseded_at IS NOT NULL`` → AI decided this was contradicted.

        Args:
            loser_id:  UUID of the fact being superseded.
            winner_id: UUID of the fact that replaces it.
            reason:    Human-readable explanation (e.g. ``"contradicts: user
                       now prefers light mode"``).  Truncated to 255 chars to
                       match ``supersede_reason VARCHAR(255)``.
            scope:     Must include at minimum agent_id (scope guard prevents
                       cross-tenant supersession).

        Returns:
            True if the row was found and superseded; False if not found
            (already superseded, deleted, or wrong scope).

        Raises:
            ValueError: if scope.agent_id is missing.
        """
        _require_agent_id(scope)
        scope_sql, scope_params = _scope_predicates(scope)
        now = _now()
        truncated_reason = reason[:255]

        sql = f"""
            UPDATE {self._TABLE}
            SET superseded_by = ?,
                superseded_at = ?,
                supersede_reason = ?,
                updated_at = ?,
                version = version + 1
            WHERE id = ?
              AND {scope_sql}
              AND deleted_at IS NULL
              AND superseded_at IS NULL
        """  # nosec B608 — _TABLE is a hardcoded class constant; scope_sql contains only literal column=? fragments from _scope_predicates (all values bound). DECISIONS.md VER-5.
        params = [winner_id, now, truncated_reason, now, loser_id, *scope_params]

        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            affected = cur.rowcount

        logger.debug(
            "supersede semantic_facts loser_id=%s winner_id=%s affected=%d",
            loser_id, winner_id, affected,
        )
        return bool(affected > 0)

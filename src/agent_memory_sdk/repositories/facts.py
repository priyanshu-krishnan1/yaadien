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
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from agent_memory_sdk.models import MemoryScope, SemanticFact
from agent_memory_sdk.repositories.base import (
    BaseRepository,
    _content_hash,
    _parse_dt,
    _parse_vector,
    _require_agent_id,
    _scope_predicates,
    _vec_to_str,
    logger,
)
from agent_memory_sdk.types import MemoryOrigin


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

    # Override _SELECT_COLS to include origin and the three supersession columns.
    # Index map (0-based):
    #   0  id          1  tenant_id   2  agent_id    3  user_id     4  thread_id
    #   5  content     6  metadata    7  embedding
    #   8  confidence  9  content_hash
    #   10 created_at  11 updated_at  12 expires_at  13 version     14 deleted_at
    #   15 origin      (TRU-1 / migration 0008)
    #   16 superseded_by  17 superseded_at  18 supersede_reason
    _SELECT_COLS = (
        "id, tenant_id, agent_id, user_id, thread_id, "
        "content, metadata, "
        "VECTOR_SERIALIZE(embedding) AS embedding, "
        "confidence, content_hash, "
        "created_at, updated_at, expires_at, version, deleted_at, "
        "origin, superseded_by, superseded_at, supersede_reason"
    )

    # Plain alias list for the outer SELECT of the ROW_NUMBER pagination
    # subquery (see BaseRepository._SELECT_OUTER_COLS).
    _SELECT_OUTER_COLS = (
        "id, tenant_id, agent_id, user_id, thread_id, "
        "content, metadata, "
        "embedding, "
        "confidence, content_hash, "
        "created_at, updated_at, expires_at, version, deleted_at, "
        "origin, superseded_by, superseded_at, supersede_reason"
    )

    def _model_from_row(self, row: tuple[Any, ...]) -> SemanticFact:
        (
            id_, tenant_id, agent_id, user_id, thread_id,
            content, metadata_str,
            embedding_str,
            confidence,
            content_hash,
            created_at, updated_at, expires_at, version, deleted_at,
            origin_str,
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
            origin=MemoryOrigin(origin_str) if origin_str else MemoryOrigin.DIRECT_WRITE,
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

    def create_many(
        self,
        records: list[tuple[SemanticFact, MemoryScope]],
        commit_every: int = 500,
    ) -> int:
        """Bulk-insert a list of (SemanticFact, MemoryScope) pairs.

        Bypasses the per-row dedup SELECT to maximize throughput for bulk
        seeding.  All records in a batch of up to ``commit_every`` rows share
        a single connection acquisition; the connection is committed and
        released after each batch.

        Unlike :meth:`~agent_memory_sdk.repositories.base.BaseRepository.create`,
        this method does **not** check for duplicate ``content_hash`` values —
        it is the caller's responsibility to ensure uniqueness (e.g. the
        benchmark seeder generates deterministically unique content per
        ``row_index``).

        Vectors are inlined as SQL string literals (the same technique as
        :meth:`~agent_memory_sdk.repositories.base.BaseRepository.create`)
        because Db2 12.1.5 fp0 does not support binding vectors via ``?``
        parameters (SQL0901N).

        Args:
            records:      List of ``(SemanticFact, MemoryScope)`` tuples.
            commit_every: Commit to Db2 after this many rows (default 500).
                          Smaller values reduce the work lost on interruption;
                          larger values reduce commit overhead.

        Returns:
            Number of rows actually inserted.
        """
        now = _now()
        inserted = 0

        i = 0
        while i < len(records):
            batch = records[i : i + commit_every]
            with self._pool.get_connection() as conn:
                cur = conn.cursor()
                for record, scope in batch:
                    _require_agent_id(scope)

                    record.agent_id = scope.agent_id
                    record.tenant_id = scope.tenant_id
                    record.user_id = scope.user_id
                    record.thread_id = scope.thread_id

                    if not record.id:
                        record.id = str(_uuid.uuid4())

                    record.created_at = now
                    record.updated_at = now
                    record.version = 1
                    record.content_hash = _content_hash(record.content)

                    # Resolve embedding — use pre-set embedding if provided,
                    # otherwise call the embedding provider.
                    if record.embedding:
                        vec_str = _vec_to_str(record.embedding)
                    elif self._embedding_provider is not None:
                        try:
                            computed = self._embedding_provider(record.content)
                            vec_str = _vec_to_str(computed)
                        except Exception:
                            logger.exception(
                                "create_many: embedding_provider raised for id=%s; "
                                "using zero-vector sentinel.",
                                record.id,
                            )
                            vec_str = self._zero_vec_str()
                    else:
                        vec_str = self._zero_vec_str()

                    metadata_str = (
                        json.dumps(record.metadata)
                        if record.metadata
                        else "{}"
                    )
                    origin_val = (
                        record.origin.value
                        if record.origin is not None
                        else "direct_write"
                    )

                    sql = f"""
                        INSERT INTO {self._TABLE} (
                            id, tenant_id, agent_id, user_id, thread_id,
                            content, metadata,
                            embedding,
                            confidence, content_hash,
                            created_at, updated_at, expires_at, version, deleted_at,
                            origin
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            ?, ?,
                            CAST('{vec_str}' AS VECTOR({self.EMBEDDING_DIM},FLOAT32)),
                            ?, ?,
                            ?, ?, ?, ?, ?,
                            ?
                        )
                    """  # nosec B608 — _TABLE is a hardcoded class constant; vec_str is validated by _vec_to_str (float-only). DECISIONS.md VER-5.
                    params = [
                        record.id,
                        record.tenant_id,
                        record.agent_id,
                        record.user_id,
                        record.thread_id,
                        record.content,
                        metadata_str,
                        float(record.confidence) if record.confidence is not None else 1.0,
                        record.content_hash,
                        record.created_at,
                        record.updated_at,
                        record.expires_at,
                        record.version,
                        record.deleted_at,
                        origin_val,
                    ]
                    cur.execute(sql, params)
                    inserted += 1
                conn.commit()
            i += commit_every

        logger.debug("create_many %s: inserted %d rows", self._TABLE, inserted)
        return inserted

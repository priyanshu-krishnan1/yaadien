"""
repositories/base.py
~~~~~~~~~~~~~~~~~~~~
Abstract base class shared by all five per-type memory repositories.

Every concrete repository:
  - Targets exactly one Db2 table (``_TABLE`` class attribute).
  - Uses the same set of columns (see 0002_memory_tables.sql).
  - Enforces that every call carries at least ``agent_id`` scope.
  - Performs soft-delete (sets ``deleted_at``) rather than hard DELETE.
  - Provides a ``search()`` method that builds the VECTOR_DISTANCE query
    with the correct FETCH EXACT / FETCH APPROX modifier.

DB-API usage notes
------------------
- Parameter placeholder: ``?`` (ibm_db_dbi uses qmark style, like SQLite).
- CLOB columns: ibm_db_dbi requires the value to be passed as a plain
  Python ``str``.  The driver converts it to CLOB internally.
- VECTOR columns: ibm_db_dbi accepts a Python ``list[float]`` for a
  VECTOR column when passed as a string in ``'[f1,f2,...]'`` notation,
  or — more portably — via the ``VECTOR_FROM_ARRAY(ARRAY[…], FLOAT32)``
  scalar constructor.  We use the ``'[f1,f2,...]'`` string form and cast
  with ``TO_VECTOR(?, FLOAT32)`` in the SQL so the parameter is a plain
  string, keeping the SQL portable across ibm_db_dbi versions.
- Timestamps: ibm_db_dbi accepts Python ``datetime`` objects for
  TIMESTAMP columns.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from agent_memory_sdk.models import MemoryScope, _MemoryBase
from agent_memory_sdk.types import DistanceMetric, SearchMode

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=_MemoryBase)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _require_agent_id(scope: MemoryScope) -> None:
    """Raise ValueError if agent_id is missing — the minimum required scope."""
    if not scope.agent_id:
        raise ValueError("MemoryScope.agent_id is required on every repository call.")


def _vec_to_str(embedding: list[float]) -> str:
    """Serialize a Python float list to the ``'[f1,f2,…]'`` string form that
    Db2's ``TO_VECTOR(?, FLOAT32)`` function accepts as a bound parameter."""
    return "[" + ",".join(str(f) for f in embedding) + "]"


def _scope_predicates(scope: MemoryScope) -> tuple[str, list[Any]]:
    """Build the WHERE clause fragment and parameter list for the scope columns.

    Always includes ``agent_id``.  Adds tenant_id, user_id, thread_id
    only when they are provided (non-None) in the scope, so narrower scopes
    filter more tightly without breaking broader queries.

    Returns:
        (sql_fragment, params)  e.g.
        ("agent_id = ? AND tenant_id = ?", ["agent-1", "tenant-a"])
    """
    parts: list[str] = ["agent_id = ?"]
    params: list[Any] = [scope.agent_id]
    if scope.tenant_id is not None:
        parts.append("tenant_id = ?")
        params.append(scope.tenant_id)
    if scope.user_id is not None:
        parts.append("user_id = ?")
        params.append(scope.user_id)
    if scope.thread_id is not None:
        parts.append("thread_id = ?")
        params.append(scope.thread_id)
    return " AND ".join(parts), params


class BaseRepository(ABC, Generic[M]):
    """Abstract base for all five memory-type repositories.

    Subclasses must set:
        _TABLE: str           — Db2 table name
        _MODEL: type[M]       — Pydantic model class

    And implement:
        _model_from_row(row)  — map a DB-API fetchone/fetchall tuple to M
    """

    _TABLE: str
    _MODEL: type[M]  # type: ignore[misc]

    # Default embedding dimension — matches 0002_memory_tables.sql.
    # Can be overridden per-instance if a different embedding model is used.
    EMBEDDING_DIM: int = 1536

    def __init__(self, pool: Any) -> None:
        """
        Args:
            pool: A ``ConnectionPool`` instance (or any object with a
                  ``get_connection()`` context-manager method returning a
                  DB-API 2.0 connection).
        """
        self._pool = pool

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @abstractmethod
    def _model_from_row(self, row: tuple[Any, ...]) -> M:
        """Construct a model instance from a DB-API result row tuple."""
        ...

    def _zero_vec_str(self) -> str:
        """Return the ``TO_VECTOR`` string for a zero-vector sentinel."""
        return "[" + ",".join("0.0" for _ in range(self.EMBEDDING_DIM)) + "]"

    # Column select list — ORDER must match _model_from_row index assumptions.
    _SELECT_COLS = (
        "id, tenant_id, agent_id, user_id, thread_id, "
        "content, metadata, "
        "VECTOR_SERIALIZE(embedding) AS embedding, "
        "created_at, updated_at, expires_at, version, deleted_at"
    )

    # ------------------------------------------------------------------
    # CRUD methods
    # ------------------------------------------------------------------

    def create(self, record: M, scope: MemoryScope) -> M:
        """Insert a new row and return the record with server-assigned timestamps.

        The record's ``agent_id`` is overwritten with ``scope.agent_id``
        to ensure consistency.  The ``id`` is generated on the Python side
        (UUID4) unless the caller already set it.

        Args:
            record:  A model instance.  ``embedding`` may be empty ([]); the
                     repo will store a zero-vector in Db2 as a sentinel.
            scope:   The caller's scope; agent_id is required.

        Returns:
            The record as stored, with created_at / updated_at set.

        Raises:
            ValueError: if scope.agent_id is missing.
        """
        _require_agent_id(scope)

        now = _now()
        record.agent_id = scope.agent_id
        record.tenant_id = scope.tenant_id
        record.user_id = scope.user_id
        record.thread_id = scope.thread_id
        record.created_at = now
        record.updated_at = now
        record.version = 1

        vec_str = _vec_to_str(record.embedding) if record.embedding else self._zero_vec_str()
        metadata_str = json.dumps(record.metadata)

        sql = f"""
            INSERT INTO {self._TABLE} (
                id, tenant_id, agent_id, user_id, thread_id,
                content, metadata, embedding,
                created_at, updated_at, expires_at, version, deleted_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, TO_VECTOR(?, FLOAT32),
                ?, ?, ?, ?, ?
            )
        """
        params = [
            record.id,
            record.tenant_id,
            record.agent_id,
            record.user_id,
            record.thread_id,
            record.content,
            metadata_str,
            vec_str,
            record.created_at,
            record.updated_at,
            record.expires_at,
            record.version,
            record.deleted_at,
        ]

        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

        logger.debug("Created %s id=%s", self._TABLE, record.id)
        return record

    def get_by_id(self, record_id: str, scope: MemoryScope) -> M | None:
        """Fetch a single non-deleted row by primary key, within scope.

        The scope check is part of the WHERE clause — a record in another
        scope will not be found even if the id is known.  This is the
        isolation boundary: callers cannot read across scopes by guessing
        IDs.

        Args:
            record_id:  The UUID string of the row.
            scope:      Must include at minimum agent_id.

        Returns:
            The model instance, or None if not found / wrong scope.
        """
        _require_agent_id(scope)
        scope_sql, scope_params = _scope_predicates(scope)

        sql = f"""
            SELECT {self._SELECT_COLS}
            FROM {self._TABLE}
            WHERE id = ?
              AND {scope_sql}
              AND deleted_at IS NULL
        """
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, [record_id, *scope_params])
            row = cur.fetchone()

        return self._model_from_row(row) if row else None

    def list(
        self,
        scope: MemoryScope,
        limit: int = 50,
        offset: int = 0,
        include_expired: bool = False,
    ) -> list[M]:
        """List non-deleted rows within scope, ordered by created_at DESC.

        Args:
            scope:           Must include at minimum agent_id.
            limit:           Max rows to return (capped at 1000).
            offset:          Rows to skip (for pagination).
            include_expired: If False (default), rows where
                             ``expires_at < NOW()`` are excluded.

        Returns:
            A list of model instances.
        """
        _require_agent_id(scope)
        scope_sql, scope_params = _scope_predicates(scope)
        limit = min(limit, 1000)

        extra = ""
        if not include_expired:
            extra = " AND (expires_at IS NULL OR expires_at > CURRENT TIMESTAMP)"

        sql = f"""
            SELECT {self._SELECT_COLS}
            FROM {self._TABLE}
            WHERE {scope_sql}
              AND deleted_at IS NULL
              {extra}
            ORDER BY created_at DESC
            FETCH FIRST ? ROWS ONLY
        """
        # Db2 doesn't support OFFSET in all configurations; handle via ROW_NUMBER
        # for paginated requests, but keep simple FETCH FIRST for offset=0 to avoid
        # unnecessary overhead.
        if offset > 0:
            sql = f"""
                SELECT * FROM (
                    SELECT {self._SELECT_COLS},
                           ROW_NUMBER() OVER (ORDER BY created_at DESC) AS rn
                    FROM {self._TABLE}
                    WHERE {scope_sql}
                      AND deleted_at IS NULL
                      {extra}
                ) WHERE rn > ? AND rn <= ?
            """
            params = [*scope_params, offset, offset + limit]
        else:
            params = [*scope_params, limit]

        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        return [self._model_from_row(r) for r in rows]

    def soft_delete(self, record_id: str, scope: MemoryScope) -> bool:
        """Tombstone a row by setting deleted_at to now.

        Never issues a hard DELETE.  The row remains in the table and can
        be recovered or audited until purge_expired() removes it.

        Args:
            record_id: UUID of the row to tombstone.
            scope:     Must include at minimum agent_id.

        Returns:
            True if the row was found and tombstoned; False if not found
            (already deleted or wrong scope).
        """
        _require_agent_id(scope)
        scope_sql, scope_params = _scope_predicates(scope)

        sql = f"""
            UPDATE {self._TABLE}
            SET deleted_at = ?, updated_at = ?, version = version + 1
            WHERE id = ?
              AND {scope_sql}
              AND deleted_at IS NULL
        """
        now = _now()
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, [now, now, record_id, *scope_params])
            conn.commit()
            affected = cur.rowcount

        logger.debug("soft_delete %s id=%s affected=%d", self._TABLE, record_id, affected)
        return affected > 0

    # ------------------------------------------------------------------
    # Vector search
    # ------------------------------------------------------------------

    def search(
        self,
        query_embedding: list[float],
        scope: MemoryScope,
        top_k: int = 10,
        metric: DistanceMetric = DistanceMetric.COSINE,
        mode: SearchMode = SearchMode.EXACT,
        include_expired: bool = False,
    ) -> list[M]:
        """Semantic search via Db2 VECTOR_DISTANCE.

        Filters by scope first, then ranks by vector distance.

        SQL shape (EXACT mode)::

            SELECT ... FROM <table>
            WHERE <scope predicates>
              AND deleted_at IS NULL
            ORDER BY VECTOR_DISTANCE(embedding, TO_VECTOR(?, FLOAT32), <metric>)
            FETCH FIRST <top_k> ROWS ONLY

        SQL shape (APPROX mode)::

            SELECT ... FROM <table>
            WHERE <scope predicates>
              AND deleted_at IS NULL
            ORDER BY VECTOR_DISTANCE(embedding, TO_VECTOR(?, FLOAT32), <metric>)
            FETCH FIRST <top_k> ROWS ONLY APPROX

        For APPROX to engage the DiskANN index, the metric MUST match the
        index's ``WITH DISTANCE COSINE`` clause (all tables use COSINE).

        Args:
            query_embedding: The embedding to search against.
            scope:           Must include at minimum agent_id.
            top_k:           Number of results to return (capped at 200).
            metric:          Distance metric (should be COSINE to use the index).
            mode:            EXACT (default), APPROX, or DEFAULT.
            include_expired: If False, expired rows are excluded.

        Returns:
            A list of model instances ordered by ascending distance
            (nearest first).
        """
        _require_agent_id(scope)
        if not query_embedding:
            raise ValueError("query_embedding must be a non-empty list of floats.")

        top_k = min(top_k, 200)
        scope_sql, scope_params = _scope_predicates(scope)
        vec_str = _vec_to_str(query_embedding)

        extra = ""
        if not include_expired:
            extra = " AND (expires_at IS NULL OR expires_at > CURRENT TIMESTAMP)"

        # Build the FETCH clause suffix.
        if mode == SearchMode.APPROX:
            fetch_suffix = "APPROX"
        else:
            fetch_suffix = ""  # EXACT and DEFAULT use plain FETCH FIRST

        approx_clause = f" {fetch_suffix}".rstrip()

        sql = f"""
            SELECT {self._SELECT_COLS}
            FROM {self._TABLE}
            WHERE {scope_sql}
              AND deleted_at IS NULL
              {extra}
            ORDER BY VECTOR_DISTANCE(embedding, TO_VECTOR(?, FLOAT32), {metric.value})
            FETCH FIRST ? ROWS ONLY{approx_clause}
        """
        params = [*scope_params, vec_str, top_k]

        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        return [self._model_from_row(r) for r in rows]

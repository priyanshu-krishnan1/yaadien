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

Step-4 additions
----------------
- ``forget(id, scope)``      — soft-delete (tombstone) a row.
- ``update(record, scope)``  — update content/metadata/embedding with
                               optimistic concurrency on ``version``.
- ``purge_expired(scope)``   — maintenance-only hard-DELETE for all
                               tombstoned rows (``deleted_at IS NOT NULL``).
                               TTL-expired but non-tombstoned rows are left
                               alone — callers must call ``forget()`` first.
                               Never called automatically.

DB-API usage notes
------------------
- Parameter placeholder: ``?`` (ibm_db_dbi uses qmark style, like SQLite).
- CLOB columns: ibm_db_dbi requires the value to be passed as a plain
  Python ``str``.  The driver converts it to CLOB internally.
- VECTOR columns: Db2 12.1.5 fp0 does NOT support binding a vector string
  via ``?`` with ``TO_VECTOR(?, FLOAT32)`` — the driver raises
  ``SQL0901N`` (binding error) for any form of ``CAST(? AS VECTOR)`` or
  ``TO_VECTOR(?)``.  The only working approach on this version is to
  **inline the vector string as a literal** directly in the SQL:
  ``CAST('{vec_str}' AS VECTOR({dim},FLOAT32))``.
  The vector string is constructed from Python floats by ``_vec_to_str``
  and contains no user input, so there is no SQL-injection risk.
- Timestamps: ibm_db_dbi accepts Python ``datetime`` objects for
  TIMESTAMP columns.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from agent_memory_sdk.exceptions import StaleWriteError
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
    """Serialize a Python float list to the ``'[f1,f2,…]'`` string form used
    as an inlined SQL literal: ``CAST('{vec_str}' AS VECTOR(dim,FLOAT32))``."""
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


def _parse_vector(val: Any) -> list[float]:
    """Convert the VECTOR_SERIALIZE output (a string like ``'[0.1,0.2,…]'``)
    back to a Python list.  Returns an empty list on None or parse error."""
    if val is None:
        return []
    if isinstance(val, list):
        return [float(x) for x in val]
    s = str(val).strip()
    if not s:
        return []
    # Strip surrounding brackets if present
    s = s.lstrip("[").rstrip("]")
    return [float(x) for x in s.split(",") if x.strip()]


def _parse_dt(val: Any) -> datetime | None:
    """Coerce a DB-API TIMESTAMP value to a Python datetime, or None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    # Some DB-API drivers return a string
    if isinstance(val, str):
        return datetime.fromisoformat(val)
    return None


class BaseRepository(ABC, Generic[M]):
    """Abstract base for all five memory-type repositories.

    Subclasses must set:
        _TABLE: str           — Db2 table name
        _MODEL: type[M]       — Pydantic model class

    And implement:
        _model_from_row(row)  — map a DB-API fetchone/fetchall tuple to M
    """

    _TABLE: str
    _MODEL: type[M]

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
        """Return the vector string for a zero-vector sentinel (inlined in SQL)."""
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
                ?, ?, CAST('{vec_str}' AS VECTOR({self.EMBEDDING_DIM},FLOAT32)),
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

    def list_all(
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

        # Db2 CURRENT TIMESTAMP uses server local time; subtract CURRENT TIMEZONE
        # to convert to UTC so it matches the UTC values we store from Python.
        extra = ""
        if not include_expired:
            extra = " AND (expires_at IS NULL OR expires_at > CURRENT TIMESTAMP - CURRENT TIMEZONE)"

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

        Alias kept for backwards-compatibility; prefer :meth:`forget`.

        Args:
            record_id: UUID of the row to tombstone.
            scope:     Must include at minimum agent_id.

        Returns:
            True if the row was found and tombstoned; False if not found
            (already deleted or wrong scope).
        """
        return self.forget(record_id, scope)

    def forget(self, record_id: str, scope: MemoryScope) -> bool:
        """Tombstone a row by setting ``deleted_at`` to now.

        Never issues a hard DELETE.  The row remains in the table for
        audit / recovery purposes and is excluded from all normal reads
        (``get_by_id``, ``list_all``, ``search``) because every query
        filters on ``deleted_at IS NULL``.

        To permanently remove rows, call :meth:`purge_expired` from a
        maintenance script or cron job — it is never invoked automatically.

        Args:
            record_id: UUID of the row to tombstone.
            scope:     Must include at minimum agent_id.

        Returns:
            True if the row was found and tombstoned; False if not found
            (already deleted or wrong scope).

        Raises:
            ValueError: if scope.agent_id is missing.
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

        logger.debug("forget %s id=%s affected=%d", self._TABLE, record_id, affected)
        return bool(affected > 0)

    def update(self, record: M, scope: MemoryScope) -> M:
        """Update ``content``, ``metadata``, and ``embedding`` for an existing row.

        Uses **optimistic concurrency**: the UPDATE is conditioned on
        ``version = record.version``.  If another writer has incremented the
        version since the caller fetched the record, the update is rejected
        with :exc:`StaleWriteError`.

        The ``version`` is atomically incremented by 1 on success, and
        ``updated_at`` is refreshed.

        Only the three mutable fields are changed:
        - ``content``
        - ``metadata``
        - ``embedding``

        Scope fields (agent_id, tenant_id, user_id, thread_id), ``id``,
        ``created_at``, and ``deleted_at`` are never modified by this method.

        Args:
            record: The model instance with the desired new ``content``,
                    ``metadata``, and ``embedding`` values.  The ``version``
                    field must equal the current DB version (as returned by
                    ``get_by_id`` or ``create``).
            scope:  Must include at minimum agent_id.

        Returns:
            The updated record with ``version`` incremented by 1 and
            ``updated_at`` refreshed.

        Raises:
            ValueError:       if scope.agent_id is missing.
            StaleWriteError:  if the row's version in DB != record.version
                              (concurrent writer detected).
        """
        _require_agent_id(scope)
        scope_sql, scope_params = _scope_predicates(scope)

        now = _now()
        vec_str = _vec_to_str(record.embedding) if record.embedding else self._zero_vec_str()
        metadata_str = json.dumps(record.metadata)
        new_version = record.version + 1

        sql = f"""
            UPDATE {self._TABLE}
            SET content = ?,
                metadata = ?,
                embedding = CAST('{vec_str}' AS VECTOR({self.EMBEDDING_DIM},FLOAT32)),
                updated_at = ?,
                version = ?
            WHERE id = ?
              AND {scope_sql}
              AND version = ?
              AND deleted_at IS NULL
        """
        params = [
            record.content,
            metadata_str,
            now,
            new_version,
            record.id,
            *scope_params,
            record.version,   # optimistic lock check
        ]

        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            affected = cur.rowcount

        if not affected:
            raise StaleWriteError(
                f"Stale write on {self._TABLE} id={record.id!r}: "
                f"expected version={record.version} but the row was modified "
                f"concurrently (or does not exist / is deleted)."
            )

        record.version = new_version
        record.updated_at = now
        logger.debug("update %s id=%s new_version=%d", self._TABLE, record.id, new_version)
        return record

    def purge_expired(self, scope: MemoryScope) -> int:
        """Hard-delete rows eligible for permanent removal within *scope*.

        **All tombstoned rows are eligible for purge** — the only condition is
        ``deleted_at IS NOT NULL``.  Rows with
        an ``expires_at`` in the past but NOT yet tombstoned are left alone —
        the normal read filters exclude them from query results, but they are
        not deleted until the caller explicitly tombstones them first with
        :meth:`forget` and then runs this method.

        This method must be called explicitly — from a cron job or a
        maintenance script (see ``scripts/purge_expired.py``).  It is never
        invoked automatically by the SDK.

        Args:
            scope: Must include at minimum agent_id.  Purge is always
                   scoped so that cross-tenant/agent data is never touched.

        Returns:
            Number of rows hard-deleted.

        Raises:
            ValueError: if scope.agent_id is missing.
        """
        _require_agent_id(scope)
        scope_sql, scope_params = _scope_predicates(scope)

        sql = f"""
            DELETE FROM {self._TABLE}
            WHERE deleted_at IS NOT NULL
              AND {scope_sql}
        """
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, scope_params)
            conn.commit()
            deleted = cur.rowcount

        logger.info("purge_expired %s scope=%s deleted=%d", self._TABLE, scope, deleted)
        return int(deleted)

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
            extra = " AND (expires_at IS NULL OR expires_at > CURRENT TIMESTAMP - CURRENT TIMEZONE)"

        # Build the FETCH clause suffix.
        fetch_suffix = "APPROX" if mode == SearchMode.APPROX else ""  # EXACT/DEFAULT: plain FETCH FIRST

        approx_clause = f" {fetch_suffix}".rstrip()

        # Db2 12.1.5 fp0 cannot combine VECTOR_SERIALIZE() in the SELECT list
        # with VECTOR_DISTANCE() in the ORDER BY in a single statement.
        # Work-around: two-step query —
        #   Step 1: fetch IDs in nearest-first order (no VECTOR_SERIALIZE in SELECT).
        #   Step 2: fetch full rows (with VECTOR_SERIALIZE) by those IDs, then
        #           reorder to restore the original nearest-first ordering.
        sql_ids = f"""
            SELECT id
            FROM {self._TABLE}
            WHERE {scope_sql}
              AND deleted_at IS NULL
              {extra}
            ORDER BY VECTOR_DISTANCE(embedding, CAST('{vec_str}' AS VECTOR({self.EMBEDDING_DIM},FLOAT32)), {metric.value})
            FETCH FIRST ? ROWS ONLY{approx_clause}
        """
        id_params = [*scope_params, top_k]

        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_ids, id_params)
            ordered_ids: list[str] = [r[0] for r in cur.fetchall()]

        if not ordered_ids:
            return []

        placeholders = ",".join("?" for _ in ordered_ids)
        sql_rows = f"""
            SELECT {self._SELECT_COLS}
            FROM {self._TABLE}
            WHERE id IN ({placeholders})
              AND deleted_at IS NULL
        """
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_rows, ordered_ids)
            raw_rows = cur.fetchall()

        # Reorder to restore nearest-first ordering from step 1.
        row_map = {r[0]: r for r in raw_rows}
        return [
            self._model_from_row(row_map[id_])
            for id_ in ordered_ids
            if id_ in row_map
        ]

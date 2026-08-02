"""
repositories/chunks.py
~~~~~~~~~~~~~~~~~~~~~~
Repository for the shared ``memory_chunks`` table (ORC-2).

``memory_chunks`` stores overlapping text chunks for parent records whose
content exceeds the configurable chunking threshold.  Each chunk has its
own embedding so that semantic search can match against fine-grained
sub-portions of long content rather than a single (diluted) embedding for
the whole CLOB.

This module provides three operations used by :class:`BaseRepository`:

- :meth:`ChunkRepository.insert_chunk` — write one chunk row.
- :meth:`ChunkRepository.delete_by_source` — delete all chunks for a given
  parent (called before re-writing chunks on ``update()``).
- :meth:`ChunkRepository.search_chunks` — rank chunks by vector distance,
  return ``(source_id, distance)`` pairs for parent resolution.

The ``search_chunks`` return type is intentionally minimal: callers receive
just enough information to (a) deduplicate to the best distance per parent,
and (b) fetch the parent rows via ``IN (...)``.  Full row retrieval is
deliberately left to the caller (:meth:`BaseRepository._search_via_chunks`)
so that the standard scope + deleted_at + confidence predicates on the
*parent* table are still applied there.

Design note (shared table vs. per-type tables)
---------------------------------------------
See ``0006_memory_chunks.sql`` for the full justification.  Short version:
all five memory types use the same VECTOR(1536,FLOAT32)/COSINE shape, so a
``source_table VARCHAR(64)`` discriminator is sufficient and a shared table
is operationally simpler (one VECTOR INDEX, one migration entry, one
repository class).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.repositories.base import (
    _parse_dt,
    _parse_vector,
    _require_agent_id,
    _scope_predicates,
    _vec_to_str,
)
from agent_memory_sdk.types import DistanceMetric, SearchMode

logger = logging.getLogger(__name__)

# Embedding dimension — must match the parent repositories.
# Overridden by MemoryStore when it wires everything together.
_DEFAULT_DIM = 1536


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class ChunkRepository:
    """CRUD and search operations on the ``memory_chunks`` table.

    Constructed by :class:`~agent_memory_sdk.store.MemoryStore` and injected
    into every per-type repository via
    :attr:`~agent_memory_sdk.repositories.base.BaseRepository._chunk_repo`.

    Callers that construct repositories directly (e.g. tests) can pass
    ``chunk_repo=None`` (the default) to disable chunking, or construct a
    ``ChunkRepository`` explicitly and pass it in.

    Args:
        pool:          A connection-pool instance (same contract as used by
                       :class:`~agent_memory_sdk.repositories.base.BaseRepository`).
        embedding_dim: Dimension of the VECTOR column.  Must match the parent
                       repositories and the DDL (default 1536).
    """

    def __init__(self, pool: Any, embedding_dim: int = _DEFAULT_DIM) -> None:
        self._pool = pool
        self.embedding_dim = embedding_dim

    def _zero_vec_str(self) -> str:
        return "[" + ",".join("0.0" for _ in range(self.embedding_dim)) + "]"

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def insert_chunk(
        self,
        source_table: str,
        source_id: str,
        chunk_index: int,
        chunk_text: str,
        embedding: list[float],
        scope: MemoryScope,
    ) -> str:
        """Insert a single chunk row and return the new chunk ``id``.

        Args:
            source_table: Db2 table name of the parent record
                          (e.g. ``"working_memory"``).
            source_id:    ``id`` of the parent row.
            chunk_index:  Zero-based ordinal of this chunk in the split sequence.
            chunk_text:   The chunk text (at most ``chunk_size`` characters).
            embedding:    Vector embedding for this chunk.  Must be non-empty;
                          a zero-vector sentinel is not meaningful here because
                          the whole point is to embed each chunk distinctly.
            scope:        Scope from the parent row — used to populate the scope
                          columns so that chunk searches can pre-filter by scope.

        Returns:
            The UUID ``id`` of the inserted chunk row.
        """
        _require_agent_id(scope)
        chunk_id = str(uuid.uuid4())
        vec_str = _vec_to_str(embedding) if embedding else self._zero_vec_str()
        now = _now()

        sql = f"""
            INSERT INTO memory_chunks (
                id, source_table, source_id, chunk_index, chunk_text,
                embedding,
                tenant_id, agent_id, user_id, thread_id,
                created_at
            ) VALUES (
                ?, ?, ?, ?, CAST(? AS CLOB(4096)),
                CAST('{vec_str}' AS VECTOR({self.embedding_dim},FLOAT32)),
                ?, ?, ?, ?,
                ?
            )
        """  # nosec B608 — table name "memory_chunks" is a hardcoded string literal; vec_str from _vec_to_str() (float() coercion guard, DECISIONS.md VER-5); embedding_dim is an int constant; all other values are bound params. CAST(? AS CLOB) avoids ibm_db SQL_CHAR binding truncation on Db2 12.1.
        params = [
            chunk_id,
            source_table,
            source_id,
            chunk_index,
            chunk_text,
            scope.tenant_id,
            scope.agent_id,
            scope.user_id,
            scope.thread_id,
            now,
        ]
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()

        logger.debug(
            "insert_chunk source_table=%s source_id=%s chunk_index=%d id=%s",
            source_table, source_id, chunk_index, chunk_id,
        )
        return chunk_id

    def delete_by_source(
        self,
        source_id: str,
        source_table: str,
        scope: MemoryScope,
    ) -> int:
        """Hard-delete all chunk rows for *source_id* / *source_table*.

        Called before re-writing chunks when a parent record is updated, so
        stale chunks from the previous version are removed first.  Also
        callable as a maintenance step when a parent record is tombstoned
        (soft-deleted) — callers who care about freeing chunk storage can
        call this explicitly; the SDK does not call it automatically on
        ``forget()``.

        Args:
            source_id:    ``id`` of the parent row.
            source_table: Db2 table name of the parent row.
            scope:        Scope from the parent row (guards against
                          cross-agent chunk deletion).

        Returns:
            Number of chunk rows deleted.
        """
        _require_agent_id(scope)
        sql = """
            DELETE FROM memory_chunks
            WHERE source_id = ?
              AND source_table = ?
              AND agent_id = ?
        """
        params: list[Any] = [source_id, source_table, scope.agent_id]
        if scope.tenant_id is not None:
            sql = sql.rstrip() + "\n              AND tenant_id = ?"
            params.append(scope.tenant_id)

        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            deleted = cur.rowcount

        logger.debug(
            "delete_by_source source_table=%s source_id=%s deleted=%d",
            source_table, source_id, deleted,
        )
        return int(deleted)

    def erase_by_scope(self, scope: MemoryScope) -> int:
        """Hard-delete every ``memory_chunks`` row matching *scope* (PIPE-5).

        Unlike :meth:`delete_by_source` (which targets the chunks of one
        specific parent row, e.g. when rewriting chunks on ``update()``),
        this deletes **every** chunk row for *scope* regardless of which
        parent table or record it belongs to. Used by
        :meth:`~agent_memory_sdk.store.MemoryStore.erase_all` so a compliance
        erasure request also removes any chunked content fragments — chunks
        have no ``deleted_at``/tombstone lifecycle of their own, so this is
        the only way to remove them in bulk for a scope.

        This is irreversible: there is no soft-delete step for
        ``memory_chunks`` rows.

        Args:
            scope: Must include at minimum agent_id.

        Returns:
            Number of chunk rows hard-deleted.

        Raises:
            ValueError: if scope.agent_id is missing.
        """
        _require_agent_id(scope)
        scope_sql, scope_params = _scope_predicates(scope)

        sql = f"""
            DELETE FROM memory_chunks
            WHERE {scope_sql}
        """  # nosec B608 — table name "memory_chunks" is a hardcoded string literal; scope_sql contains only literal column=? fragments (all values bound). DECISIONS.md VER-5.
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, scope_params)
            conn.commit()
            deleted = cur.rowcount

        logger.info("erase_by_scope memory_chunks scope=%s deleted=%d", scope, deleted)
        return int(deleted)

    # ------------------------------------------------------------------
    # PIPE-6: enumerate all chunks for a scope (export_scope() support)
    # ------------------------------------------------------------------

    def list_all(
        self,
        scope: MemoryScope,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List every chunk row within *scope*, ordered by created_at then chunk_index.

        Unlike :meth:`BaseRepository.list_all`, ``memory_chunks`` has no
        ``deleted_at`` lifecycle column of its own — chunk rows are hard-deleted
        and rewritten wholesale by :meth:`delete_by_source` whenever their
        parent record is updated, so there is nothing to filter here. This
        method always returns every chunk row matching *scope*.

        Used by :meth:`~agent_memory_sdk.store.MemoryStore.export_scope`
        (PIPE-6) to enumerate ``memory_chunks`` rows for backup/portability.
        There is no ``ChunkRepository``-specific Pydantic model (chunks are
        an implementation detail of the per-type repositories), so rows are
        returned as plain dicts rather than model instances.

        Args:
            scope:  Must include at minimum agent_id.
            limit:  Max rows to return (capped at 1000).
            offset: Rows to skip (for pagination).

        Returns:
            A list of dicts, one per chunk row, with keys: ``id``,
            ``source_table``, ``source_id``, ``chunk_index``, ``chunk_text``,
            ``embedding`` (``list[float]``), ``tenant_id``, ``agent_id``,
            ``user_id``, ``thread_id``, ``created_at`` (``datetime | None``).

        Raises:
            ValueError: if scope.agent_id is missing.
        """
        _require_agent_id(scope)
        scope_sql, scope_params = _scope_predicates(scope)
        limit = min(limit, 1000)

        select_cols = (
            "id, source_table, source_id, chunk_index, chunk_text, "
            "VECTOR_SERIALIZE(embedding) AS embedding, "
            "tenant_id, agent_id, user_id, thread_id, created_at"
        )

        if offset > 0:
            sql = f"""
                SELECT * FROM (
                    SELECT {select_cols},
                           ROW_NUMBER() OVER (ORDER BY created_at ASC, chunk_index ASC) AS rn
                    FROM memory_chunks
                    WHERE {scope_sql}
                ) WHERE rn > ? AND rn <= ?
            """  # nosec B608 — table name "memory_chunks" is a hardcoded string literal; scope_sql contains only literal column=? fragments from _scope_predicates (all values bound). DECISIONS.md VER-5.
            params: list[Any] = [*scope_params, offset, offset + limit]
        else:
            sql = f"""
                SELECT {select_cols}
                FROM memory_chunks
                WHERE {scope_sql}
                ORDER BY created_at ASC, chunk_index ASC
                FETCH FIRST ? ROWS ONLY
            """  # nosec B608 — table name "memory_chunks" is a hardcoded string literal; scope_sql contains only literal column=? fragments from _scope_predicates (all values bound). DECISIONS.md VER-5.
            params = [*scope_params, limit]

        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            (
                id_, source_table, source_id, chunk_index, chunk_text,
                embedding_str, tenant_id, agent_id, user_id, thread_id, created_at,
            ) = row[:11]
            result.append({
                "id": id_,
                "source_table": source_table,
                "source_id": source_id,
                "chunk_index": chunk_index,
                "chunk_text": chunk_text,
                "embedding": _parse_vector(embedding_str),
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "user_id": user_id,
                "thread_id": thread_id,
                "created_at": _parse_dt(created_at),
            })
        return result

    def list_all_for_scope(self, scope: MemoryScope) -> list[dict[str, Any]]:
        """Return all ``memory_chunks`` rows for *scope* as plain dicts (for export).

        This is the export-facing alias for :meth:`list_all` used by
        :meth:`~agent_memory_sdk.store.MemoryStore.export_scope` (PIPE-6).
        Returns raw column dicts with keys: ``id``, ``source_table``,
        ``source_id``, ``chunk_index``, ``chunk_text``, ``embedding``
        (``list[float]``), ``tenant_id``, ``agent_id``, ``user_id``,
        ``thread_id``, ``created_at``.  Does NOT go through a Pydantic model —
        chunks are exported as raw dicts tagged with ``_type: "memory_chunk"``
        by the caller.

        Args:
            scope: Must include at minimum agent_id.

        Returns:
            All matching rows (no internal page cap — iterates via
            :meth:`list_all` in 1000-row pages until exhausted).

        Raises:
            ValueError: if scope.agent_id is missing.
        """
        _require_agent_id(scope)
        results: list[dict[str, Any]] = []
        offset = 0
        page_size = 1000
        while True:
            page = self.list_all(scope, limit=page_size, offset=offset)
            results.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return results

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_chunks(
        self,
        query_embedding: list[float],
        source_table: str,
        scope: MemoryScope,
        top_n: int = 40,
        metric: DistanceMetric = DistanceMetric.COSINE,
        mode: SearchMode = SearchMode.EXACT,
    ) -> list[tuple[str, float]]:
        """Rank chunk rows by vector distance, return ``(source_id, distance)`` pairs.

        Uses the same two-step ID-fetch → full-row-fetch pattern as the parent
        ``BaseRepository.search()`` to work around the Db2 12.1.5 fp0
        constraint that ``VECTOR_SERIALIZE`` and ``VECTOR_DISTANCE`` cannot be
        combined in a single statement.  Here we only need ``source_id`` and the
        distance, so step 2 is a lightweight ``SELECT id, source_id`` instead of
        fetching the full row.

        The Db2 12.1.5 fp0 limitation means ``VECTOR_DISTANCE`` cannot appear in
        the SELECT list alongside ``VECTOR_SERIALIZE`` in the ORDER BY, but it
        *can* be used in ORDER BY alongside a plain column SELECT.  So step 1
        here returns ``(id, source_id)`` ordered by distance, which is all we
        need for the parent-resolution step.

        Args:
            query_embedding: Query vector.
            source_table:    Only chunks whose ``source_table`` matches this
                             value are considered.
            scope:           Must include at minimum agent_id.
            top_n:           Maximum number of chunk rows to return
                             (capped at 800 to prevent runaway queries).
            metric:          Distance metric (COSINE to use the index).
            mode:            EXACT or APPROX.

        Returns:
            List of ``(source_id, distance)`` tuples ordered by ascending
            distance (nearest chunk first).  Each tuple represents one
            matching chunk; multiple tuples may have the same ``source_id``
            (different chunks of the same parent).  The caller
            (:meth:`~agent_memory_sdk.repositories.base.BaseRepository._search_via_chunks`)
            deduplicates to the best distance per ``source_id``.
        """
        if not query_embedding:
            raise ValueError("query_embedding must be a non-empty list of floats.")
        _require_agent_id(scope)
        top_n = min(top_n, 800)

        scope_sql, scope_params = _scope_predicates(scope)
        vec_str = _vec_to_str(query_embedding)
        fetch_suffix = "APPROX" if mode == SearchMode.APPROX else ""
        approx_clause = f" {fetch_suffix}".rstrip()

        # Step 1 — rank chunks by distance, return (id, source_id) ordered
        # nearest-first.  No VECTOR_SERIALIZE here so the Db2 fp0 limit
        # is not triggered.
        sql_rank = f"""
            SELECT id, source_id
            FROM memory_chunks
            WHERE source_table = ?
              AND {scope_sql}
            ORDER BY VECTOR_DISTANCE(
                embedding,
                CAST('{vec_str}' AS VECTOR({self.embedding_dim},FLOAT32)),
                {metric.value}
            )
            FETCH FIRST ? ROWS ONLY{approx_clause}
        """  # nosec B608 — table name is a hardcoded literal; vec_str from _vec_to_str() (float() guard, DECISIONS.md VER-5); metric.value is a DistanceMetric enum (hardcoded strings); scope_sql contains only literal column=? fragments (bound params).
        rank_params = [source_table, *scope_params, top_n]

        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_rank, rank_params)
            ranked_rows = cur.fetchall()

        if not ranked_rows:
            return []

        # Step 2 — re-fetch with the actual VECTOR_DISTANCE value so we
        # can return distances for the dedup step.  We fetch them by the
        # ranked chunk IDs to get (source_id, distance) without needing
        # VECTOR_SERIALIZE.
        chunk_ids = [r[0] for r in ranked_rows]
        id_to_source: dict[str, str] = {r[0]: r[1] for r in ranked_rows}
        placeholders = ",".join("?" for _ in chunk_ids)

        sql_dist = f"""
            SELECT id,
                   VECTOR_DISTANCE(
                       embedding,
                       CAST('{vec_str}' AS VECTOR({self.embedding_dim},FLOAT32)),
                       {metric.value}
                   ) AS dist
            FROM memory_chunks
            WHERE id IN ({placeholders})
        """  # nosec B608 — table name hardcoded; vec_str from _vec_to_str() (float() guard, DECISIONS.md VER-5); metric.value is a DistanceMetric enum; placeholders is a literal "?,?…" string; chunk IDs passed as bound params.
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_dist, chunk_ids)
            dist_rows = cur.fetchall()

        dist_map: dict[str, float] = {r[0]: float(r[1]) for r in dist_rows}

        # Reconstruct in the original rank order (from step 1), pairing
        # each chunk id with its source_id and actual distance.
        result: list[tuple[str, float]] = []
        for chunk_id in chunk_ids:
            source_id = id_to_source.get(chunk_id)
            distance = dist_map.get(chunk_id)
            if source_id is not None and distance is not None:
                result.append((source_id, distance))

        return result

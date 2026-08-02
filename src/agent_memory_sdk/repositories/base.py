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

ORC-2 additions
---------------
- ``_split_chunks(text, chunk_size, chunk_overlap)``
    Pure utility: splits *text* into overlapping fixed-size character
    chunks.  Returns a list of strings.  No-ops when len(text) <=
    chunk_size (returns [text] — same as the caller just not chunking).
- Chunking gate ``_HAS_CHUNKS = True`` on every repository by default.
    When a record's content exceeds ``CHUNK_THRESHOLD``, ``create()`` and
    ``update()`` write chunk rows to the shared ``memory_chunks`` table
    (via an injected ``ChunkRepository``) and replace the parent's embedding
    with a zero-vector sentinel so the NOT NULL constraint is satisfied —
    semantic search on the parent row's embedding column is superseded by
    chunk-level search.  When content length ≤ ``CHUNK_THRESHOLD``, the
    path is identical to pre-ORC-2 behaviour: a single embedding on the
    parent row, no chunk rows.

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
   The vector string is constructed by ``_vec_to_str``, which coerces every
   element through ``float()`` before formatting.  Any non-numeric value
   raises ``ValueError``/``TypeError`` before it reaches SQL, which closes
   the injection path at all three call sites (create, update, search).
   For create/update the source is a Pydantic-validated ``list[float]``
   field, so coercion is a no-op.  For search the source is the
   ``query_embedding`` parameter, which is an unenforced type hint and is
   externally reachable via the MCP ``recall`` tool — coercion here is the
   actual security boundary.
- Timestamps: ibm_db_dbi accepts Python ``datetime`` objects for
  TIMESTAMP columns.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from agent_memory_sdk.repositories.chunks import ChunkRepository

from agent_memory_sdk.exceptions import InvalidMetadataFilterError, StaleWriteError
from agent_memory_sdk.models import MemoryScope, _MemoryBase
from agent_memory_sdk.types import DistanceMetric, SearchMode

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=_MemoryBase)


# ---------------------------------------------------------------------------
# Chunking defaults (ORC-2)
# ---------------------------------------------------------------------------

#: Content-length threshold above which a record is split into chunks.
#: Records at or below this length use a single embedding on the parent row
#: (identical to pre-ORC-2 behaviour; no chunk rows are created).
CHUNK_THRESHOLD: int = 2000

#: Maximum characters per chunk (default).
CHUNK_SIZE: int = 800

#: Overlap between consecutive chunks in characters (default).
CHUNK_OVERLAP: int = 200


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _split_chunks(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """Split *text* into overlapping fixed-size character chunks.

    Chunks are extracted using a sliding window of width *chunk_size* and
    step ``(chunk_size - chunk_overlap)``.  The final chunk always extends
    to the end of the string, even if it is shorter than *chunk_size*.

    When ``len(text) <= chunk_size``, a single-element list ``[text]`` is
    returned — identical to no chunking.

    Args:
        text:          The text to chunk.
        chunk_size:    Maximum number of characters per chunk (default 800).
        chunk_overlap: Number of characters that adjacent chunks share
                       (default 200).  Must be < chunk_size.

    Returns:
        A non-empty list of string chunks.

    Raises:
        ValueError: if ``chunk_size < 1`` or ``chunk_overlap >= chunk_size``.
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1; got {chunk_size!r}.")
    if chunk_overlap >= chunk_size:
        raise ValueError(
            f"chunk_overlap must be < chunk_size; "
            f"got chunk_overlap={chunk_overlap!r}, chunk_size={chunk_size!r}."
        )
    if len(text) <= chunk_size:
        return [text]
    step = chunk_size - chunk_overlap
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += step
    return chunks


def _content_hash(content: str) -> str:
    """Compute the hex SHA-256 of *normalized* content.

    Normalization steps (must be applied in this exact order, consistently
    everywhere a content_hash is computed or compared):

    1. **Lowercase** — ``content.lower()``
    2. **Whitespace-collapse** — replace every run of one-or-more whitespace
       characters (space, tab, newline, carriage-return, form-feed, etc.) with
       a single ASCII space, then strip leading/trailing whitespace.
    3. **SHA-256** — hash the UTF-8 encoding of the normalized string and
       return the hex digest (64 lowercase hex characters).

    The result is stored in ``content_hash VARCHAR(64)`` in Db2.
    """
    normalized = re.sub(r"\s+", " ", content.lower()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _require_agent_id(scope: MemoryScope) -> None:
    """Raise ValueError if agent_id is missing — the minimum required scope."""
    if not scope.agent_id:
        raise ValueError("MemoryScope.agent_id is required on every repository call.")


def _vec_to_str(embedding: list[float]) -> str:
    """Serialize a Python float list to the ``'[f1,f2,…]'`` string form used
    as an inlined SQL literal: ``CAST('{vec_str}' AS VECTOR(dim,FLOAT32))``.

    Every element is coerced through ``float()`` before formatting.  This
    raises ``ValueError`` / ``TypeError`` for any non-numeric value, which
    prevents SQL injection at all three call sites (create, update, search).
    For create/update the source is a Pydantic-validated ``list[float]``,
    so the coercion is a no-op.  For search the source is the caller-supplied
    ``query_embedding`` argument, where coercion is the actual security guard.
    """
    return "[" + ",".join(str(float(f)) for f in embedding) + "]"


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


# ---------------------------------------------------------------------------
# Supported metadata filter operators (ORC-3)
# ---------------------------------------------------------------------------

#: The complete set of operator keys recognized inside a field's value dict.
#: Any key starting with ``$`` that is NOT in this set raises
#: :exc:`~agent_memory_sdk.exceptions.InvalidMetadataFilterError`.
_KNOWN_OPERATORS: frozenset[str] = frozenset(
    {"$not", "$array_contains", "$array_contains_any"}
)


def _build_metadata_filter(
    metadata_filter: dict[str, Any] | None,
) -> tuple[str, list[Any]]:
    """Translate a ``metadata_filter`` dict into SQL predicates on the ``metadata`` column.

    The ``metadata`` column is ``VARCHAR(4096)`` JSON text.  Db2 12.1 supports
    ``JSON_VALUE(col, '$.field')`` to extract a scalar and ``JSON_EXISTS(col,
    '$.field?(@ == "value")')`` for richer path-expression predicates.

    **Supported operator set (ORC-3 — deliberately small):**

    1. **Exact match** — scalar equality on a top-level field::

           {"source": "support"}
           → JSON_EXISTS(metadata, '$.source?(@ == "support")') = 'true'
             (value inlined; see security note below)

    2. **$not** — scalar inequality::

           {"status": {"$not": "archived"}}
           → JSON_EXISTS(metadata, '$.status?(@ != "archived")') = 'true'
             (value inlined; see security note below)

    3. **$array_contains** — single value must appear in a JSON array field::

           {"tags": {"$array_contains": "urgent"}}
           → JSON_EXISTS(metadata, '$.tags[*]?(@ == "urgent")') = 'true'
             (no bound param — value inlined in the path expression; see security note)

    4. **$array_contains_any** — at least one of the supplied values must
       appear in a JSON array field::

           {"tags": {"$array_contains_any": ["urgent", "bug"]}}
           → ( JSON_EXISTS(metadata, '$.tags[*]?(@ == "urgent")') = 'true'
               OR JSON_EXISTS(metadata, '$.tags[*]?(@ == "bug")') = 'true' )
             (no bound params — values inlined; see security note)

    **Security note — all values are inlined in JSON_EXISTS path expressions:**
    All four operators use ``JSON_EXISTS`` path expressions (SQL string
    literals) — ibm_db_dbi on Db2 12.1.5 fp0 does not support binding values
    into path expressions via ``?``, and attempting to bind values for
    ``JSON_VALUE(col, path) = ?`` comparisons triggers a segmentation fault in
    the ``ibm_db`` C extension (null pointer dereference in the C-level
    statement descriptor lookup).  All values are therefore inlined: any
    ``'`` (single-quote) is doubled to ``''`` and any ``\\`` is doubled to
    ``\\\\`` before interpolation.  Non-string values (int, float, bool) are
    formatted directly and are inherently safe.  As a result,
    ``_build_metadata_filter`` **never** appends to ``params`` — it always
    returns an empty params list, eliminating the ibm_db bound-parameter
    mismatch entirely.

    **Field-name safety:**
    Field names are validated against the pattern ``^[A-Za-z_][A-Za-z0-9_.]*$``
    before interpolation.  Any field name that does not match raises
    :exc:`~agent_memory_sdk.exceptions.InvalidMetadataFilterError`.

    **Unrecognized operator keys:**
    Any key inside a value dict that starts with ``$`` and is not in
    :data:`_KNOWN_OPERATORS` raises
    :exc:`~agent_memory_sdk.exceptions.InvalidMetadataFilterError` immediately.
    Unrecognized keys that do *not* start with ``$`` are treated as nested objects
    and will raise ``InvalidMetadataFilterError`` (nested objects beyond the top
    level are not supported).

    Args:
        metadata_filter: A flat dict mapping field names to either a scalar
            (exact match) or a dict with one operator key.  ``None`` is a no-op
            and returns an empty string + empty params list.

    Returns:
        ``(sql_fragment, params)`` where *sql_fragment* is zero or more
        ``AND <predicate>`` clauses (leading space included) ready to be
        appended to an existing WHERE clause, and *params* is always an empty
        list (all filter values are inlined in JSON path expressions — no ``?``
        placeholders are emitted by this function).

    Raises:
        InvalidMetadataFilterError: if a field name is invalid, a value dict
            contains an unrecognised ``$``-prefixed operator, or a non-scalar /
            non-dict value is supplied as the field operand.
    """
    if not metadata_filter:
        return "", []

    _FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

    parts: list[str] = []
    params: list[Any] = []

    for field, operand in metadata_filter.items():
        # Validate field name to prevent SQL injection via field names.
        if not _FIELD_RE.match(field):
            raise InvalidMetadataFilterError(
                f"Invalid metadata field name {field!r}: field names must match "
                r"^[A-Za-z_][A-Za-z0-9_.]*$"
            )

        if isinstance(operand, dict):
            # Operator dict: {"$not": "val"}, {"$array_contains": ...}, etc.
            for op_key in operand:
                if op_key.startswith("$") and op_key not in _KNOWN_OPERATORS:
                    raise InvalidMetadataFilterError(
                        f"Unrecognized metadata filter operator {op_key!r} on field "
                        f"{field!r}. Supported operators: "
                        + ", ".join(sorted(_KNOWN_OPERATORS))
                        + ". For exact match, pass a scalar value directly."
                    )
                if not op_key.startswith("$"):
                    raise InvalidMetadataFilterError(
                        f"Nested object operand on field {field!r} is not supported. "
                        f"Use a scalar value (exact match) or a recognized operator "
                        f"dict (e.g. {{\"$not\": \"value\"}})."
                    )

            if "$not" in operand:
                val = operand["$not"]
                escaped = _escape_json_path_value(val)
                parts.append(
                    f"JSON_EXISTS(metadata, '$.{field}?(@ != {escaped})')"
                    " = 'true'"
                )

            elif "$array_contains" in operand:
                val = operand["$array_contains"]
                escaped = _escape_json_path_value(val)
                parts.append(
                    f"JSON_EXISTS(metadata, '$.{field}[*]?(@ == {escaped})')"
                    " = 'true'"
                )

            elif "$array_contains_any" in operand:
                vals = operand["$array_contains_any"]
                if not isinstance(vals, (list, tuple)) or len(vals) == 0:
                    raise InvalidMetadataFilterError(
                        f"$array_contains_any on field {field!r} requires a "
                        f"non-empty list of values; got {vals!r}."
                    )
                sub_parts = [
                    f"JSON_EXISTS(metadata, '$.{field}[*]?(@ == {_escape_json_path_value(v)})')"
                    " = 'true'"
                    for v in vals
                ]
                parts.append("(" + " OR ".join(sub_parts) + ")")

        elif isinstance(operand, bool):
            # bool must be checked before int/float because bool is an int subclass.
            # Use JSON_EXISTS to avoid ibm_db segfault with bound params on JSON_VALUE.
            escaped = _escape_json_path_value(operand)
            parts.append(
                f"JSON_EXISTS(metadata, '$.{field}?(@ == {escaped})')"
                " = 'true'"
            )
        elif isinstance(operand, (str, int, float)) or operand is None:
            # Exact match — use JSON_EXISTS (inlined value) to avoid the ibm_db
            # C-extension segfault that occurs when a bound ? parameter is used
            # with JSON_VALUE(col, path) = ? on Db2 12.1.5 fp0 / ibm_db 3.2.9.
            escaped = _escape_json_path_value(operand)
            parts.append(
                f"JSON_EXISTS(metadata, '$.{field}?(@ == {escaped})')"
                " = 'true'"
            )

        else:
            raise InvalidMetadataFilterError(
                f"Unsupported operand type {type(operand).__name__!r} for field "
                f"{field!r}. Use a scalar value (exact match) or a recognized "
                f"operator dict."
            )

    if not parts:
        return "", []

    sql = " AND " + " AND ".join(parts)
    return sql, params


def _escape_json_path_value(val: Any) -> str:
    """Escape *val* for safe inline use in a Db2 JSON_EXISTS path expression.

    Returns the value formatted as a SQL-safe string or numeric literal:

    - **str** — wrapped in double-quotes with ``"`` escaped as ``\\"``,
      ``\\`` escaped as ``\\\\``, and ``'`` escaped as ``''`` (SQL
      single-quote doubling so the outer SQL literal stays valid).
    - **int / float** — returned as a plain numeric string (no quotes).
    - **bool** — returned as ``true`` or ``false`` (JSON boolean literals,
      not quoted — Db2's path-expression evaluator understands JSON booleans
      natively).
    - **None** — returned as ``null``.
    - Any other type — ``repr()`` as a fallback (safe because the caller
      validates field names separately and this value is not user-provided SQL).

    The caller is responsible for wrapping the result in the full path
    expression string, e.g.::

        f"'$.tags[*]?(@ == {_escape_json_path_value(\"urgent\")})'".
    """
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if val is None:
        return "null"
    # String: escape backslash first (to avoid double-escaping), then
    # double-quote (for JSON path), then single-quote (for SQL literal).
    s = str(val)
    s = s.replace("\\", "\\\\")   # \ → \\  (JSON path backslash)
    s = s.replace('"', '\\"')      # " → \"  (JSON path string boundary)
    s = s.replace("'", "''")       # ' → ''  (SQL literal single-quote)
    return f'"{s}"'


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


# ---------------------------------------------------------------------------
# Hybrid retrieval helpers (PIPE-1)
# ---------------------------------------------------------------------------

#: Standard RRF constant — rank 1 contributes 1/(60+1), rank 60 contributes
#: 1/(60+60) = 1/120.  60 is the widely-cited default (Cormack et al. 2009).
_RRF_K: int = 60


def _keyword_tokens(text: str) -> frozenset[str]:
    """Tokenise *text* into a frozenset of lowercase alphanumeric tokens.

    Splitting on any non-alphanumeric character produces tokens that are
    robust to punctuation and whitespace variations.  The frozenset allows
    fast set-intersection in :func:`_rrf_fuse`.

    Args:
        text: Any string — query or record content.

    Returns:
        Frozenset of lowercase word tokens.  Empty string → empty frozenset.
    """
    return frozenset(re.findall(r"[a-z0-9]+", text.lower()))


def _rrf_fuse(
    vector_order: list[str],
    keyword_order: list[str],
    k: int = _RRF_K,
) -> list[str]:
    """Fuse two ranked ID lists via Reciprocal Rank Fusion (RRF).

    RRF score for a document ``d`` is::

        score(d) = sum( 1 / (k + rank_i(d)) )

    where the sum is taken over every ranked list that contains ``d`` and
    ``rank_i(d)`` is its 1-based position in that list.  Documents absent
    from a list contribute nothing for that list.  The fused result is
    returned in descending score order (highest-scoring first).

    Args:
        vector_order:  IDs ordered by ascending vector distance
                       (nearest = rank 1 = highest contribution).
        keyword_order: IDs ordered by descending keyword-overlap score
                       (most overlap = rank 1 = highest contribution).
        k:             RRF smoothing constant (default :data:`_RRF_K` = 60).

    Returns:
        Deduplicated list of IDs in descending RRF-score order.
    """
    scores: dict[str, float] = {}
    for rank, id_ in enumerate(vector_order, start=1):
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank)
    for rank, id_ in enumerate(keyword_order, start=1):
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda i: scores[i], reverse=True)


class BaseRepository(ABC, Generic[M]):
    """Abstract base for all five memory-type repositories.

    Subclasses must set:
        _TABLE: str           — Db2 table name
        _MODEL: type[M]       — Pydantic model class

    And implement:
        _model_from_row(row)  — map a DB-API fetchone/fetchall tuple to M

    Class attributes that control write behaviour (override in subclasses):
        _DEDUP_ON_WRITE: bool — When True (default), ``create()`` issues a
            dedup SELECT before every INSERT.  Set to False in repositories
            where the same content legitimately repeats (WorkingMemory), so
            the round-trip is skipped entirely.
        _HAS_SUPERSESSION: bool — When True, ``list_all()``, ``search()``,
            and the ``create()`` dedup-check all append
            ``AND superseded_at IS NULL`` to their WHERE clauses.  Only
            ``SemanticFactRepository`` sets this to True because only the
            ``semantic_facts`` table has the supersession columns (added in
            migration 0004).  All other repositories leave this as False so
            that their SQL never references a column that doesn't exist in
            their schema — referencing a missing column is a compile-time
            SQL error (Db2 SQLCODE -206), not a vacuous truth.
        _HAS_CONSOLIDATED_AT: bool — When True, the SELECT column list
            includes ``consolidated_at`` and ``_claim_consolidated()`` is
            available.  Only ``WorkingMemoryRepository`` and
            ``EpisodicMemoryRepository`` set this to True because only
            ``working_memory`` and ``episodic_memory`` have this column
            (added by migration 0005 / ENH-4).  All other repositories leave
            this as False so their SQL never references a missing column.

    ORC-2 (content chunking):
        The repository accepts an optional ``chunk_repo`` argument at
        construction time.  When set and a record's content exceeds
        ``chunk_threshold``, ``create()`` and ``update()`` write chunk rows
        to ``memory_chunks`` via that repository and store a zero-vector
        sentinel on the parent row.  When ``chunk_repo`` is ``None`` (the
        default when not wired in), chunking is silently skipped — backward-
        compatible with any code that constructs a repository directly.
    """

    _TABLE: str
    _MODEL: type[M]

    # Default embedding dimension — matches 0002_memory_tables.sql.
    # Can be overridden per-instance if a different embedding model is used.
    EMBEDDING_DIM: int = 1536

    # Write-time dedup gate (ENH-2).  Repositories whose content is
    # legitimately repetitive (e.g. WorkingMemory conversation turns) should
    # override this to False so create() skips the dedup SELECT entirely —
    # no wasted round-trip, and the append-only ordering is preserved.
    _DEDUP_ON_WRITE: bool = True

    # Supersession gate (ENH-3 fix).  Only semantic_facts has the three
    # supersession columns (superseded_by, superseded_at, supersede_reason)
    # added by migration 0004.  Only SemanticFactRepository overrides this
    # to True.  When False (all other repos), the "AND superseded_at IS NULL"
    # fragment is never added to SQL so queries never reference a missing column.
    _HAS_SUPERSESSION: bool = False

    # consolidated_at gate (ENH-4).  Only working_memory and episodic_memory
    # have this column (migration 0005).  WorkingMemoryRepository and
    # EpisodicMemoryRepository override this to True; all others leave it
    # False so their SQL never references a missing column (Db2 SQLCODE -206).
    _HAS_CONSOLIDATED_AT: bool = False

    def __init__(
        self,
        pool: Any,
        chunk_repo: ChunkRepository | None = None,
        chunk_threshold: int = CHUNK_THRESHOLD,
        chunk_size: int = CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
    ) -> None:
        """
        Args:
            pool:            A ``ConnectionPool`` instance (or any object with a
                             ``get_connection()`` context-manager method returning a
                             DB-API 2.0 connection).
            chunk_repo:      An optional :class:`~agent_memory_sdk.repositories.chunks.ChunkRepository`
                             instance.  When provided and a record's content
                             exceeds *chunk_threshold*, ``create()`` and
                             ``update()`` write overlapping chunk rows to
                             ``memory_chunks`` and store a zero-vector sentinel
                             on the parent embedding column.  ``None`` (default)
                             disables chunking — backward-compatible.
            chunk_threshold: Content-length threshold in characters above which
                             chunking is applied (default :data:`CHUNK_THRESHOLD`
                             = 2000).
            chunk_size:      Maximum characters per chunk (default
                             :data:`CHUNK_SIZE` = 800).
            chunk_overlap:   Overlap in characters between adjacent chunks
                             (default :data:`CHUNK_OVERLAP` = 200).
        """
        self._pool = pool
        self._chunk_repo: ChunkRepository | None = chunk_repo
        self._chunk_threshold: int = chunk_threshold
        self._chunk_size: int = chunk_size
        self._chunk_overlap: int = chunk_overlap
        # embedding_provider is injected by MemoryStore after construction
        # (or by a caller who wants chunk-level embeddings).  None means
        # "no provider wired in" — chunking will be skipped even if chunk_repo
        # is set, because there is nothing to call for per-chunk embeddings.
        self._embedding_provider: Any = None

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

    def _write_chunks(
        self,
        source_id: str,
        content: str,
        embedding_provider: Any,
        scope: MemoryScope,
    ) -> int:
        """Split *content* into chunks, embed each, and persist to ``memory_chunks``.

        Only called when all three conditions hold:
        1. ``self._chunk_repo`` is not None.
        2. ``len(content) > self._chunk_threshold``.
        3. *embedding_provider* is not None.

        If *embedding_provider* is None (e.g. the caller hasn't wired one in)
        this method is a no-op and returns 0 — the parent row retains whatever
        embedding was provided (or the zero-vector sentinel).

        Args:
            source_id:          The ``id`` of the parent row just inserted/updated.
            content:            The full content string to chunk.
            embedding_provider: Any callable ``(text: str) -> list[float]``.
                                Pass ``None`` to skip chunking.
            scope:              Scope from the parent record (used to populate
                                scope columns on each chunk row).

        Returns:
            Number of chunk rows written.
        """
        if self._chunk_repo is None or embedding_provider is None:
            return 0
        chunks = _split_chunks(content, self._chunk_size, self._chunk_overlap)
        # Delete any stale chunks from a previous write of this source row
        # before inserting fresh ones.  This is the update case: on create(),
        # no stale chunks exist (new row), so delete_by_source is a no-op.
        self._chunk_repo.delete_by_source(source_id, self._TABLE, scope)
        written = 0
        for idx, chunk_text in enumerate(chunks):
            try:
                emb = embedding_provider(chunk_text)
            except Exception:
                logger.exception(
                    "_write_chunks: embedding_provider raised for source_id=%s chunk_index=%d; "
                    "skipping this chunk.",
                    source_id, idx,
                )
                continue
            self._chunk_repo.insert_chunk(
                source_table=self._TABLE,
                source_id=source_id,
                chunk_index=idx,
                chunk_text=chunk_text,
                embedding=emb,
                scope=scope,
            )
            written += 1
        logger.debug(
            "_write_chunks %s source_id=%s chunks=%d", self._TABLE, source_id, written
        )
        return written

    # Column select list — ORDER must match _model_from_row index assumptions.
    # Index map (0-based):
    #   0  id          1  tenant_id   2  agent_id    3  user_id     4  thread_id
    #   5  content     6  metadata    7  embedding
    #   8  confidence  9  content_hash
    #   10 created_at  11 updated_at  12 expires_at  13 version     14 deleted_at
    #
    # Repos with _HAS_CONSOLIDATED_AT=True (working_memory, episodic_memory)
    # override _SELECT_COLS to append:
    #   15 consolidated_at
    _SELECT_COLS = (
        "id, tenant_id, agent_id, user_id, thread_id, "
        "content, metadata, "
        "VECTOR_SERIALIZE(embedding) AS embedding, "
        "confidence, content_hash, "
        "created_at, updated_at, expires_at, version, deleted_at"
    )

    # ------------------------------------------------------------------
    # CRUD methods
    # ------------------------------------------------------------------

    def create(self, record: M, scope: MemoryScope) -> M:
        """Insert a new row and return the record with server-assigned timestamps.

        **Write-time deduplication (ENH-2):** when ``_DEDUP_ON_WRITE`` is
        True (the default), the method computes the hex SHA-256 of the
        normalized content (lowercased, then whitespace-collapsed) and checks
        whether a non-deleted row with the same ``(agent_id scope,
        content_hash)`` already exists.  If one is found, that existing row is
        returned immediately — no new row is inserted.  This makes repeated
        writes of the same content idempotent for fact-style repositories
        (SemanticFact, EntityProfile, ProceduralMemory).

        When ``_DEDUP_ON_WRITE`` is False (WorkingMemory), the dedup SELECT is
        **skipped entirely** — no wasted round-trip.  Working-memory is an
        ordered append-only conversation log where short repeated utterances
        ("ok", "yes", "thanks") are expected and must each produce a distinct
        row to preserve correct turn count and ordering.

        **Concurrency note (best-effort only):** the dedup check is *not*
        atomic.  The SELECT and subsequent INSERT are two separate statements
        with no transaction or row lock between them.  Under concurrent writers
        to the same scope with identical content, both can pass the SELECT
        before either INSERT lands, resulting in duplicate rows.  There is no
        DB-level uniqueness backstop (no UNIQUE constraint — see
        project-management/DECISIONS.md ENH-2 entry for the reasoning).  This is safe for the common
        single-writer or low-concurrency case; it is not a uniqueness guarantee
        under high-concurrency writers to the same scope.

        The dedup check uses both ``deleted_at IS NULL`` and
        ``superseded_at IS NULL`` so that superseded rows do not block
        new writes of content with the same hash — a superseded fact is
        no longer the live version of that claim and must not act as a
        dedup barrier for a fresh write.

        The record's ``agent_id`` is overwritten with ``scope.agent_id``
        to ensure consistency.  The ``id`` is generated on the Python side
        (UUID4) unless the caller already set it.

        Args:
            record:  A model instance.  ``embedding`` may be empty ([]); the
                     repo will store a zero-vector in Db2 as a sentinel.
            scope:   The caller's scope; agent_id is required.

        Returns:
            The record as stored, with created_at / updated_at set.  When
            ``_DEDUP_ON_WRITE`` is True and a duplicate was detected, the
            *existing* row is returned instead.

        Raises:
            ValueError: if scope.agent_id is missing.
        """
        _require_agent_id(scope)

        now = _now()
        record.agent_id = scope.agent_id
        record.tenant_id = scope.tenant_id
        record.user_id = scope.user_id
        record.thread_id = scope.thread_id

        # --- ENH-2: compute content_hash ------------------------------------
        h = _content_hash(record.content)
        record.content_hash = h

        if self._DEDUP_ON_WRITE:
            scope_sql, scope_params = _scope_predicates(scope)
            supersession_sql = " AND superseded_at IS NULL" if self._HAS_SUPERSESSION else ""
            dedup_sql = f"""
                SELECT {self._SELECT_COLS}
                FROM {self._TABLE}
                WHERE {scope_sql}
                  AND content_hash = ?
                  AND deleted_at IS NULL
                  {supersession_sql}
                FETCH FIRST 1 ROWS ONLY
            """  # nosec B608 — _TABLE/_SELECT_COLS are hardcoded class constants; scope_sql contains only literal column=? fragments from _scope_predicates (all values bound); supersession_sql is a hardcoded string fragment. DECISIONS.md VER-5.
            with self._pool.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(dedup_sql, [*scope_params, h])
                existing_row = cur.fetchone()

            if existing_row is not None:
                logger.debug(
                    "Dedup hit on %s content_hash=%s — returning existing id=%s",
                    self._TABLE, h[:8], existing_row[0],
                )
                return self._model_from_row(existing_row)
        # --- end dedup check ------------------------------------------------

        record.created_at = now
        record.updated_at = now
        record.version = 1

        # --- ORC-2: chunking gate -------------------------------------------
        # When the content exceeds the threshold AND a chunk_repo is wired in,
        # replace the parent row's embedding with a zero-vector sentinel and
        # delegate semantic content to per-chunk embeddings.  The actual chunk
        # writes happen AFTER the parent INSERT so that the source_id FK
        # (soft-reference; no DB-level foreign key) already exists.
        should_chunk = (
            self._chunk_repo is not None
            and self._embedding_provider is not None
            and len(record.content) > self._chunk_threshold
        )
        if should_chunk:
            # Content is long enough and an embedding provider is available.
            # Store a zero-vector sentinel on the parent row — the NOT NULL
            # constraint is satisfied and it's clear that this row's semantic
            # representation lives in the memory_chunks table, not here.
            parent_vec_str = self._zero_vec_str()
        elif self._embedding_provider is not None and not record.embedding:
            # No chunking (content below threshold or chunking disabled), but
            # an embedding provider is wired in and the caller did not
            # pre-compute an embedding.  Compute it now so that the parent row
            # carries a real semantic vector and search() can rank it correctly.
            # This is the normal path when enable_chunking=False is set on the
            # MemoryStore (e.g. the benchmark's consolidator-wired local store)
            # or when the caller writes a WorkingMemory without pre-embedding.
            try:
                computed_vec = self._embedding_provider(record.content)
                parent_vec_str = _vec_to_str(computed_vec)
            except Exception:
                logger.exception(
                    "create: embedding_provider raised for %s id=%s; "
                    "falling back to zero-vector sentinel.",
                    self._TABLE, record.id,
                )
                parent_vec_str = self._zero_vec_str()
        else:
            parent_vec_str = _vec_to_str(record.embedding) if record.embedding else self._zero_vec_str()
        # --- end ORC-2 gate -------------------------------------------------

        vec_str = parent_vec_str
        metadata_str = json.dumps(record.metadata)

        sql = f"""
            INSERT INTO {self._TABLE} (
                id, tenant_id, agent_id, user_id, thread_id,
                content, metadata, embedding,
                confidence, content_hash,
                created_at, updated_at, expires_at, version, deleted_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, CAST('{vec_str}' AS VECTOR({self.EMBEDDING_DIM},FLOAT32)),
                ?, ?,
                ?, ?, ?, ?, ?
            )
        """  # nosec B608 — _TABLE is a hardcoded class constant; vec_str is produced by _vec_to_str() which coerces every element through float() before formatting (injection guard established in DECISIONS.md VER-5); all column values are bound params.
        params = [
            record.id,
            record.tenant_id,
            record.agent_id,
            record.user_id,
            record.thread_id,
            record.content,
            metadata_str,
            record.confidence,
            record.content_hash,
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

        # --- ORC-2: write chunk rows after parent INSERT --------------------
        if should_chunk:
            self._write_chunks(record.id, record.content, self._embedding_provider, scope)
        # --- end ORC-2 chunk write ------------------------------------------

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
        """  # nosec B608 — _TABLE/_SELECT_COLS are hardcoded class constants; scope_sql contains only literal column=? fragments; all values are bound params. DECISIONS.md VER-5.
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
        min_confidence: float = 0.0,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[M]:
        """List non-deleted, non-superseded rows within scope, ordered by created_at DESC.

        Args:
            scope:           Must include at minimum agent_id.
            limit:           Max rows to return (capped at 1000).
            offset:          Rows to skip (for pagination).
            include_expired: If False (default), rows where
                             ``expires_at < NOW()`` are excluded.
            min_confidence:  If > 0.0, only rows with ``confidence >=
                             min_confidence`` are returned.  Applied after
                             the ``deleted_at IS NULL`` and ``expires_at``
                             filters; rows below the threshold are excluded
                             regardless of their freshness or scope.
                             Defaults to 0.0 (no confidence filter, full
                             backward compatibility).
            metadata_filter: Optional dict of metadata predicates (ORC-3).
                             Supported operators:

                             - **Exact match** — ``{"source": "support"}``
                             - **$not** — ``{"status": {"$not": "archived"}}``
                             - **$array_contains** —
                               ``{"tags": {"$array_contains": "urgent"}}``
                             - **$array_contains_any** —
                               ``{"tags": {"$array_contains_any": ["a", "b"]}}``

                             Unrecognized ``$``-prefixed operator keys raise
                             :exc:`~agent_memory_sdk.exceptions.InvalidMetadataFilterError`.
                             ``None`` (default) disables filtering — full backward
                             compatibility.

        Returns:
            A list of model instances.  Rows where ``deleted_at IS NOT NULL``
            or ``superseded_at IS NOT NULL`` are always excluded from normal
            reads.  To access superseded rows for audit purposes, query the
            table directly.

        Raises:
            ValueError: if scope.agent_id is missing.
            InvalidMetadataFilterError: if *metadata_filter* contains an
                unrecognized operator key or an invalid field name.
        """
        _require_agent_id(scope)
        scope_sql, scope_params = _scope_predicates(scope)
        limit = min(limit, 1000)

        # Db2 CURRENT TIMESTAMP uses server local time; subtract CURRENT TIMEZONE
        # to convert to UTC so it matches the UTC values we store from Python.
        extra = ""
        if not include_expired:
            extra = " AND (expires_at IS NULL OR expires_at > CURRENT TIMESTAMP - CURRENT TIMEZONE)"

        conf_sql = ""
        conf_params: list[Any] = []
        if min_confidence > 0.0:
            conf_sql = " AND confidence >= ?"
            conf_params = [min_confidence]

        supersession_sql = " AND superseded_at IS NULL" if self._HAS_SUPERSESSION else ""

        # ORC-3: translate metadata_filter dict into SQL predicates.
        meta_sql, meta_params = _build_metadata_filter(metadata_filter)

        sql = f"""
            SELECT {self._SELECT_COLS}
            FROM {self._TABLE}
            WHERE {scope_sql}
              AND deleted_at IS NULL
              {supersession_sql}
              {extra}
              {conf_sql}
              {meta_sql}
            ORDER BY created_at DESC
            FETCH FIRST ? ROWS ONLY
        """  # nosec B608 — _TABLE/_SELECT_COLS are hardcoded constants; scope_sql from _scope_predicates (bound params only); supersession_sql/extra/conf_sql are hardcoded fragments; meta_sql from _build_metadata_filter which validates field names + uses bound params. DECISIONS.md VER-5.
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
                      {supersession_sql}
                      {extra}
                      {conf_sql}
                      {meta_sql}
                ) WHERE rn > ? AND rn <= ?
            """  # nosec B608 — same safety as the FETCH FIRST path above: all interpolated fragments are hardcoded constants or outputs of vetted helpers with bound params. DECISIONS.md VER-5.
            params = [*scope_params, *conf_params, *meta_params, offset, offset + limit]
        else:
            params = [*scope_params, *conf_params, *meta_params, limit]

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
        """  # nosec B608 — _TABLE is a hardcoded class constant; scope_sql contains only literal column=? fragments (all values bound). DECISIONS.md VER-5.
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
        # Recompute content_hash so that update() keeps the hash consistent
        # with the new content value being persisted.
        new_hash = _content_hash(record.content)
        metadata_str = json.dumps(record.metadata)
        new_version = record.version + 1

        # --- ORC-2: chunking gate (update path) --------------------------------
        should_chunk = (
            self._chunk_repo is not None
            and self._embedding_provider is not None
            and len(record.content) > self._chunk_threshold
        )
        if should_chunk:
            vec_str = self._zero_vec_str()
        elif self._embedding_provider is not None and not record.embedding:
            # Same logic as create(): compute the embedding for short content
            # when a provider is wired in and no pre-computed vector is present.
            try:
                computed_vec = self._embedding_provider(record.content)
                vec_str = _vec_to_str(computed_vec)
            except Exception:
                logger.exception(
                    "update: embedding_provider raised for %s id=%s; "
                    "falling back to zero-vector sentinel.",
                    self._TABLE, record.id,
                )
                vec_str = self._zero_vec_str()
        else:
            vec_str = _vec_to_str(record.embedding) if record.embedding else self._zero_vec_str()
        # --- end ORC-2 gate -------------------------------------------------

        sql = f"""
            UPDATE {self._TABLE}
            SET content = ?,
                metadata = ?,
                embedding = CAST('{vec_str}' AS VECTOR({self.EMBEDDING_DIM},FLOAT32)),
                confidence = ?,
                content_hash = ?,
                updated_at = ?,
                version = ?
            WHERE id = ?
              AND {scope_sql}
              AND version = ?
              AND deleted_at IS NULL
        """  # nosec B608 — _TABLE hardcoded; vec_str from _vec_to_str() (float() coercion guard, DECISIONS.md VER-5); scope_sql bound-params only; EMBEDDING_DIM is an int constant.
        params = [
            record.content,
            metadata_str,
            record.confidence,
            new_hash,
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
        record.content_hash = new_hash

        # --- ORC-2: rewrite chunk rows after UPDATE -------------------------
        if should_chunk:
            self._write_chunks(record.id, record.content, self._embedding_provider, scope)
        # --- end ORC-2 chunk write ------------------------------------------

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
        """  # nosec B608 — _TABLE is a hardcoded class constant; scope_sql contains only literal column=? fragments (all values bound). DECISIONS.md VER-5.
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, scope_params)
            conn.commit()
            deleted = cur.rowcount

        logger.info("purge_expired %s scope=%s deleted=%d", self._TABLE, scope, deleted)
        return int(deleted)

    # ------------------------------------------------------------------
    # erase_all() — compliance hard-delete (PIPE-5)
    # ------------------------------------------------------------------

    def erase_all(self, scope: MemoryScope) -> int:
        """Hard-delete **every** row in this table matching *scope* — no exceptions.

        This is the per-table primitive behind
        :meth:`~agent_memory_sdk.store.MemoryStore.erase_all`. It is a
        genuine compliance ("right to erasure" / GDPR-style) hard-delete,
        deliberately different from both :meth:`forget` and
        :meth:`purge_expired`:

        - :meth:`forget` sets ``deleted_at`` (reversible tombstone); rows
          stay in the table.
        - :meth:`purge_expired` hard-deletes, but only rows that are
          **already tombstoned** (``deleted_at IS NOT NULL``).
        - :meth:`erase_all` hard-deletes **every** row matching *scope*,
          tombstoned or not, expired or not — the ``deleted_at`` /
          ``expires_at`` lifecycle is bypassed entirely.

        This method is irreversible: there is no soft-delete step, no grace
        period, and no way to recover the deleted rows afterward (short of a
        database backup). It must only be invoked in response to an explicit
        erasure request for the given scope — never as part of routine
        memory-lifecycle maintenance (use :meth:`purge_expired` for that).

        Args:
            scope: Must include at minimum agent_id.  Erasure is always
                   scoped so that cross-tenant/agent data is never touched —
                   the same scoping-enforcement discipline as every other
                   method on this class (see project-management/DECISIONS.md
                   VER-5 entry).

        Returns:
            Number of rows hard-deleted.

        Raises:
            ValueError: if scope.agent_id is missing.
        """
        _require_agent_id(scope)
        scope_sql, scope_params = _scope_predicates(scope)

        sql = f"""
            DELETE FROM {self._TABLE}
            WHERE {scope_sql}
        """  # nosec B608 — _TABLE is a hardcoded class constant; scope_sql contains only literal column=? fragments (all values bound). DECISIONS.md VER-5.
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, scope_params)
            conn.commit()
            deleted = cur.rowcount

        logger.info("erase_all %s scope=%s deleted=%d", self._TABLE, scope, deleted)
        return int(deleted)

    # ------------------------------------------------------------------
    # Claim-based consolidation locking (ENH-4)
    # ------------------------------------------------------------------

    def _claim_consolidated(self, record_id: str, scope: MemoryScope) -> bool:
        """Atomically claim a row for consolidation processing.

        Issues::

            UPDATE <table>
            SET consolidated_at = <now>
            WHERE id = ? AND <scope> AND consolidated_at IS NULL

        and checks the rowcount.  A rowcount of 1 means this worker
        successfully claimed the row.  A rowcount of 0 means another
        concurrent worker already claimed it — the row should be skipped.

        This implements the basic idempotency/claim lock described in the
        original ``consolidate_pending.py`` docstring: the claim is
        **best-effort, not transactional**.  Under normal conditions (one
        worker, or low-concurrency workers with a small batch size) this
        prevents double-processing reliably.  Under extreme concurrency
        (many workers, very large batch, unusually slow worker) two workers
        could in theory call ``_claim_consolidated`` on the same row before
        either UPDATE lands; only the first UPDATE to arrive at Db2 will
        match ``consolidated_at IS NULL`` and get rowcount=1.  The second
        will get rowcount=0 and skip the row.  Db2's row-level locking
        ensures the two UPDATEs are serialized at the engine level.

        Only available on repositories where ``_HAS_CONSOLIDATED_AT`` is
        True (``working_memory``, ``episodic_memory``).  Raises
        ``NotImplementedError`` on other repositories so callers get an
        actionable error rather than a silent SQL failure referencing a
        nonexistent column.

        Args:
            record_id: UUID of the row to claim.
            scope:     Must include at minimum agent_id.

        Returns:
            True if this worker successfully claimed the row (rowcount == 1).
            False if another worker already claimed it (rowcount == 0).

        Raises:
            NotImplementedError: if ``_HAS_CONSOLIDATED_AT`` is False.
            ValueError:          if scope.agent_id is missing.
        """
        if not self._HAS_CONSOLIDATED_AT:
            raise NotImplementedError(
                f"{self.__class__.__name__} does not have a consolidated_at "
                f"column (table={self._TABLE!r}).  Only WorkingMemoryRepository "
                f"and EpisodicMemoryRepository support _claim_consolidated()."
            )

        _require_agent_id(scope)
        scope_sql, scope_params = _scope_predicates(scope)
        now = _now()

        sql = f"""
            UPDATE {self._TABLE}
            SET consolidated_at = ?
            WHERE id = ?
              AND {scope_sql}
              AND consolidated_at IS NULL
        """  # nosec B608 — _TABLE is a hardcoded class constant; scope_sql contains only literal column=? fragments (all values bound). DECISIONS.md VER-5.
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, [now, record_id, *scope_params])
            conn.commit()
            claimed = cur.rowcount

        logger.debug(
            "_claim_consolidated %s id=%s claimed=%s",
            self._TABLE, record_id, bool(claimed),
        )
        return bool(claimed)

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
        min_confidence: float = 0.0,
        search_chunks: bool | None = None,
        metadata_filter: dict[str, Any] | None = None,
        hybrid: bool = False,
        query_text: str = "",
    ) -> list[M]:
        """Semantic search via Db2 VECTOR_DISTANCE.

        Filters by scope first, then ranks by vector distance.

        SQL shape (EXACT mode, standard path)::

            SELECT ... FROM <table>
            WHERE <scope predicates>
              AND deleted_at IS NULL
            ORDER BY VECTOR_DISTANCE(embedding, TO_VECTOR(?, FLOAT32), <metric>)
            FETCH FIRST <top_k> ROWS ONLY

        SQL shape (APPROX mode, standard path)::

            SELECT ... FROM <table>
            WHERE <scope predicates>
              AND deleted_at IS NULL
            ORDER BY VECTOR_DISTANCE(embedding, TO_VECTOR(?, FLOAT32), <metric>)
            FETCH FIRST <top_k> ROWS ONLY APPROX

        For APPROX to engage the DiskANN index, the metric MUST match the
        index's ``WITH DISTANCE COSINE`` clause (all tables use COSINE).

        **ORC-2: chunk-based search (``search_chunks`` parameter)**

        When chunking is active on this repository (``self._chunk_repo is not
        None``), records whose content exceeded the threshold at write time have
        a zero-vector sentinel on the parent row — they cannot be found via the
        standard parent-embedding search path.  The ``search_chunks`` parameter
        controls how this is handled:

        - ``None`` (**default**) — *auto-detect*: use chunk-aware search
          automatically when ``self._chunk_repo is not None`` (i.e. chunking is
          actually active for this store); otherwise use the standard path.
          This is the recommended default — callers do not need to know whether
          chunking is configured.
        - ``True`` — always use chunk-aware search, even if ``chunk_repo`` is
          ``None`` (in that case the call silently falls back to the standard
          path — the same safe fallback as before).  Use this to force the chunk
          path, e.g. when you know all content is long.
        - ``False`` — always use the standard parent-embedding path.  Use this
          to bypass chunk search, e.g. to avoid the extra round-trip when you
          know all stored content is short.

        The chunk-search path is a two-step ``search → resolve → dedup``
        pattern parallel to the existing two-step ID-rank → full-row-fetch
        pattern already used for the standard search path:

        1. **Chunk search** — rank ``memory_chunks`` by distance to
           *query_embedding*, filtered to ``agent_id`` scope and
           ``source_table = <this table>``, returning up to ``top_k * 4``
           rows (over-fetch to compensate for multiple chunks per parent).
        2. **Resolve parents** — from the chunk results, collect unique
           ``source_id`` values and fetch the corresponding parent rows via
           ``IN (...)`` with the standard scope + deleted_at predicates.
        3. **Dedup and re-rank** — deduplicate to one result per parent (keeping
           the chunk with the smallest distance as that parent's representative
           distance), then sort parents by that best-chunk distance and take
           the top *top_k*.

        This reuses the two-step reorder-after-fetch pattern from the standard
        search path (Step 7 Db2 12.1.5 fp0 compatibility workaround).

        **PIPE-1: hybrid retrieval (``hybrid`` / ``query_text`` parameters)**

        When ``hybrid=True``, the final result order is determined by
        Reciprocal Rank Fusion (RRF) of two ranked lists:

        1. **Vector ranking** — the vector-distance order returned by Db2
           (step 1 above), exactly as today.
        2. **Keyword ranking** — a Python-side token-set overlap score
           computed over the **same candidate set already fetched** — no
           additional SQL query.  Each candidate's ``content`` is tokenised
           (lowercase alphanumeric tokens) and the overlap with the tokenised
           *query_text* is counted; candidates are ranked by descending overlap
           count.

        The two ranked lists are then fused via::

            rrf_score(d) = 1 / (_RRF_K + vector_rank(d))
                         + 1 / (_RRF_K + keyword_rank(d))

        where :data:`_RRF_K` = 60 (the widely-cited standard constant from
        Cormack et al. 2009) and a document absent from a list contributes
        zero for that list.  The final result is returned in descending
        RRF-score order (highest first).

        When ``hybrid=True`` and *query_text* is an empty string (the
        default), the keyword ranking is computed against an empty token set,
        which assigns zero overlap to every candidate — the RRF score then
        contains only the vector contribution, giving the same result order
        as ``hybrid=False``.  Callers should pass the raw query string via
        *query_text* to get a meaningful keyword signal.

        **No new SQL query is issued for the keyword ranking** — the keyword
        score is computed in Python over the full rows fetched in step 2.
        This keeps hybrid search zero-mandatory-infrastructure: it requires
        no Db2 Text Search Extender, no full-text index, no external search
        engine — just the existing vector search plus a pure-Python
        tokenisation pass.

        Args:
            query_embedding: The embedding to search against.
            scope:           Must include at minimum agent_id.
            top_k:           Number of results to return (capped at 200).
            metric:          Distance metric (should be COSINE to use the index).
            mode:            EXACT (default), APPROX, or DEFAULT.
            include_expired: If False, expired rows are excluded.
            min_confidence:  If > 0.0, only rows with ``confidence >=
                             min_confidence`` are returned.  The predicate is
                             applied in the first SQL step (ID-ranking pass) so
                             low-confidence rows do not consume top_k slots.
                             Defaults to 0.0 (no confidence filter, full
                             backward compatibility).
            search_chunks:   Controls chunk-aware search (ORC-2).
                             ``None`` (default) auto-detects: chunk path is used
                             when ``self._chunk_repo is not None``, standard path
                             otherwise.  ``True`` forces the chunk path (safe
                             fallback to standard when chunk_repo is None).
                             ``False`` forces the standard parent-embedding path.
            metadata_filter: Optional dict of metadata predicates (ORC-3).
                             Applied in the first SQL step (ID-ranking pass) so
                             rows excluded by the filter do not consume top_k
                             slots.  Supported operators:

                             - **Exact match** — ``{"source": "support"}``
                             - **$not** — ``{"status": {"$not": "archived"}}``
                             - **$array_contains** —
                               ``{"tags": {"$array_contains": "urgent"}}``
                             - **$array_contains_any** —
                               ``{"tags": {"$array_contains_any": ["a", "b"]}}``

                             Unrecognized ``$``-prefixed operator keys raise
                             :exc:`~agent_memory_sdk.exceptions.InvalidMetadataFilterError`.
                             ``None`` (default) disables filtering — full backward
                             compatibility.
            hybrid:          When ``True``, fuse the vector-distance ranking
                             with a Python-side keyword-overlap ranking via
                             Reciprocal Rank Fusion (RRF, k=60).  Default
                             ``False`` — pure vector search, unchanged behavior.
            query_text:      Raw query string used for keyword tokenisation
                             when ``hybrid=True``.  Ignored when
                             ``hybrid=False``.  Default ``""`` (empty string
                             produces a zero keyword signal — equivalent to
                             pure vector ordering).

        Returns:
            A list of model instances ordered by ascending distance
            (nearest first) when ``hybrid=False``, or by descending RRF score
            (best match first) when ``hybrid=True``.

        Raises:
            ValueError: if scope.agent_id is missing or query_embedding is empty.
            InvalidMetadataFilterError: if *metadata_filter* contains an
                unrecognized operator key or an invalid field name.
        """
        # ORC-2: resolve the effective search_chunks flag.
        # None → auto-detect based on whether chunk_repo is wired in.
        effective_search_chunks: bool
        if search_chunks is None:
            effective_search_chunks = self._chunk_repo is not None
        else:
            effective_search_chunks = search_chunks

        # Route to chunk-based search when the effective flag is True and
        # a chunk_repo is actually available.
        if effective_search_chunks and self._chunk_repo is not None:
            return self._search_via_chunks(
                query_embedding=query_embedding,
                scope=scope,
                top_k=top_k,
                metric=metric,
                mode=mode,
                include_expired=include_expired,
                min_confidence=min_confidence,
                metadata_filter=metadata_filter,
                hybrid=hybrid,
                query_text=query_text,
            )
        _require_agent_id(scope)
        if not query_embedding:
            raise ValueError("query_embedding must be a non-empty list of floats.")

        top_k = min(top_k, 200)
        scope_sql, scope_params = _scope_predicates(scope)
        vec_str = _vec_to_str(query_embedding)

        extra = ""
        if not include_expired:
            extra = " AND (expires_at IS NULL OR expires_at > CURRENT TIMESTAMP - CURRENT TIMEZONE)"

        conf_sql = ""
        conf_params: list[Any] = []
        if min_confidence > 0.0:
            conf_sql = " AND confidence >= ?"
            conf_params = [min_confidence]

        # ORC-3: translate metadata_filter dict into SQL predicates.
        # Applied in step 1 (ID-ranking) so filtered-out rows don't consume top_k slots.
        meta_sql, meta_params = _build_metadata_filter(metadata_filter)

        # Build the FETCH clause suffix.
        fetch_suffix = "APPROX" if mode == SearchMode.APPROX else ""  # EXACT/DEFAULT: plain FETCH FIRST

        approx_clause = f" {fetch_suffix}".rstrip()

        # Db2 12.1.5 fp0 cannot combine VECTOR_SERIALIZE() in the SELECT list
        # with VECTOR_DISTANCE() in the ORDER BY in a single statement.
        # Work-around: two-step query —
        #   Step 1: fetch IDs in nearest-first order (no VECTOR_SERIALIZE in SELECT).
        #   Step 2: fetch full rows (with VECTOR_SERIALIZE) by those IDs, then
        #           reorder to restore the original nearest-first ordering.
        supersession_sql = " AND superseded_at IS NULL" if self._HAS_SUPERSESSION else ""

        # PIPE-1: when hybrid=True, over-fetch candidates so the keyword
        # ranking has a larger pool to reorder from before the top_k slice.
        # For hybrid=False, fetch exactly top_k (unchanged behavior).
        fetch_k = min(top_k * 4, 800) if hybrid else top_k

        sql_ids = f"""
            SELECT id
            FROM {self._TABLE}
            WHERE {scope_sql}
              AND deleted_at IS NULL
              {supersession_sql}
              {extra}
              {conf_sql}
              {meta_sql}
            ORDER BY VECTOR_DISTANCE(embedding, CAST('{vec_str}' AS VECTOR({self.EMBEDDING_DIM},FLOAT32)), {metric.value})
            FETCH FIRST ? ROWS ONLY{approx_clause}
        """  # nosec B608 — _TABLE hardcoded; vec_str from _vec_to_str() (float() guard, DECISIONS.md VER-5); metric.value is a DistanceMetric enum (hardcoded strings); scope_sql/supersession_sql/extra/conf_sql/meta_sql are hardcoded or validated fragments with bound params.
        id_params = [*scope_params, *conf_params, *meta_params, fetch_k]

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
        """  # nosec B608 — _TABLE/_SELECT_COLS are hardcoded constants; placeholders is a literal "?,?,…" string built from len(ordered_ids). DECISIONS.md VER-5.
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_rows, ordered_ids)
            raw_rows = cur.fetchall()

        # Build a stable id→row map for both the pure-vector and hybrid paths.
        row_map = {r[0]: r for r in raw_rows}

        if not hybrid:
            # Pure-vector path (unchanged behaviour): restore nearest-first
            # ordering from step 1, then return.
            return [
                self._model_from_row(row_map[id_])
                for id_ in ordered_ids
                if id_ in row_map
            ]

        # PIPE-1: hybrid path — fuse vector rank with keyword rank via RRF.
        # The vector ranking is already in ordered_ids (nearest first).
        # Build the keyword ranking over the full candidate set:
        query_tokens = _keyword_tokens(query_text)
        # Only candidates we actually have full rows for (step 2 may drop some
        # if they were concurrently deleted between the two queries).
        live_ids = [id_ for id_ in ordered_ids if id_ in row_map]
        if query_tokens:
            keyword_scores: dict[str, int] = {
                id_: len(query_tokens & _keyword_tokens(row_map[id_][5]))
                for id_ in live_ids
            }
            keyword_order = sorted(
                live_ids,
                key=lambda i: keyword_scores[i],
                reverse=True,
            )
        else:
            # No query text → no keyword signal; keyword_order same as vector.
            keyword_order = list(live_ids)

        fused_ids = _rrf_fuse(live_ids, keyword_order)[:top_k]
        return [self._model_from_row(row_map[id_]) for id_ in fused_ids]

    # ------------------------------------------------------------------
    # ORC-2: chunk-based search helpers
    # ------------------------------------------------------------------

    def _search_via_chunks(
        self,
        query_embedding: list[float],
        scope: MemoryScope,
        top_k: int = 10,
        metric: DistanceMetric = DistanceMetric.COSINE,
        mode: SearchMode = SearchMode.EXACT,
        include_expired: bool = False,
        min_confidence: float = 0.0,
        metadata_filter: dict[str, Any] | None = None,
        hybrid: bool = False,
        query_text: str = "",
    ) -> list[M]:
        """Search via the ``memory_chunks`` table, then resolve to parent records.

        Implements the three-step chunk → resolve → dedup pattern described in
        :meth:`search`.  Only called when ``self._chunk_repo is not None`` and
        ``search_chunks=True`` was passed to :meth:`search`.

        When ``hybrid=True``, the final result order is determined by RRF fusion
        of the chunk-distance ranking and a Python-side keyword-overlap ranking
        (see :meth:`search` for the full description of the hybrid mode).

        Args:
            (same as :meth:`search`, ``search_chunks`` is not forwarded here;
            ``hybrid`` and ``query_text`` are forwarded from :meth:`search`)

        Returns:
            A list of at most *top_k* parent-record model instances, ordered by
            their best-matching chunk distance (ascending, nearest first) when
            ``hybrid=False``, or by descending RRF score when ``hybrid=True``.
        """
        if not query_embedding:
            raise ValueError("query_embedding must be a non-empty list of floats.")
        _require_agent_id(scope)

        top_k = min(top_k, 200)

        # Step 1 — rank memory_chunks by distance.
        # For hybrid=True, over-fetch a larger candidate pool (same factor as
        # the standard search path) so the keyword reranking has more material
        # to work with before the top_k slice.
        overfetch = min(top_k * 4, 800)
        chunk_hits = self._chunk_repo.search_chunks(  # type: ignore[union-attr]
            query_embedding=query_embedding,
            source_table=self._TABLE,
            scope=scope,
            top_n=overfetch,
            metric=metric,
            mode=mode,
        )
        # chunk_hits: list of (source_id, distance) tuples, ordered nearest-first.
        if not chunk_hits:
            return []

        # Step 2 — dedup: keep the best (minimum) distance per source_id,
        # preserving the chunk-rank order for stable tie-breaking.
        seen: dict[str, float] = {}
        for source_id, distance in chunk_hits:
            if source_id not in seen or distance < seen[source_id]:
                seen[source_id] = distance

        # Sort by best-chunk distance, ascending (nearest first).
        # For hybrid=False, slice to top_k here.
        # For hybrid=True, keep all candidates; the RRF step will slice.
        all_ranked_ids: list[str] = [
            sid for sid, _ in sorted(seen.items(), key=lambda x: x[1])
        ]
        ranked_ids = all_ranked_ids if hybrid else all_ranked_ids[:top_k]

        if not ranked_ids:
            return []

        # Step 3 — fetch full parent rows (with VECTOR_SERIALIZE) for the
        # ranked IDs, applying scope + deleted_at + confidence + metadata filters.
        scope_sql, scope_params = _scope_predicates(scope)
        supersession_sql = " AND superseded_at IS NULL" if self._HAS_SUPERSESSION else ""

        extra = ""
        if not include_expired:
            extra = " AND (expires_at IS NULL OR expires_at > CURRENT TIMESTAMP - CURRENT TIMEZONE)"

        conf_sql = ""
        conf_params: list[Any] = []
        if min_confidence > 0.0:
            conf_sql = " AND confidence >= ?"
            conf_params = [min_confidence]

        # ORC-3: translate metadata_filter into SQL predicates.
        meta_sql, meta_params = _build_metadata_filter(metadata_filter)

        placeholders = ",".join("?" for _ in ranked_ids)
        sql_rows = f"""
            SELECT {self._SELECT_COLS}
            FROM {self._TABLE}
            WHERE id IN ({placeholders})
              AND {scope_sql}
              AND deleted_at IS NULL
              {supersession_sql}
              {extra}
              {conf_sql}
              {meta_sql}
        """  # nosec B608 — _TABLE/_SELECT_COLS hardcoded; placeholders is literal "?,?…"; scope_sql/supersession_sql/extra/conf_sql/meta_sql are hardcoded or validated fragments with bound params. DECISIONS.md VER-5.
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql_rows, [*ranked_ids, *scope_params, *conf_params, *meta_params])
            raw_rows = cur.fetchall()

        row_map = {r[0]: r for r in raw_rows}

        if not hybrid:
            # Pure chunk-distance ordering (unchanged behaviour).
            return [
                self._model_from_row(row_map[id_])
                for id_ in ranked_ids
                if id_ in row_map
            ]

        # PIPE-1: hybrid path — fuse chunk-distance rank with keyword rank via RRF.
        query_tokens = _keyword_tokens(query_text)
        live_ids = [id_ for id_ in ranked_ids if id_ in row_map]
        if query_tokens:
            keyword_scores: dict[str, int] = {
                id_: len(query_tokens & _keyword_tokens(row_map[id_][5]))
                for id_ in live_ids
            }
            keyword_order = sorted(
                live_ids,
                key=lambda i: keyword_scores[i],
                reverse=True,
            )
        else:
            keyword_order = list(live_ids)

        fused_ids = _rrf_fuse(live_ids, keyword_order)[:top_k]
        return [self._model_from_row(row_map[id_]) for id_ in fused_ids]

"""
store.py
~~~~~~~~
MemoryStore — the top-level facade that composes all five repositories.

Basic usage (Step 3 and later)::

    from agent_memory_sdk.store import MemoryStore
    from agent_memory_sdk.models import MemoryScope, WorkingMemory

    store = MemoryStore(pool)
    scope = MemoryScope(agent_id="agent-001", user_id="user-42")

    # Write
    record = store.remember(
        WorkingMemory(agent_id=scope.agent_id, content="Hello!"),
        scope,
    )

    # Soft-delete (tombstone)
    store.forget(record.id, "working", scope)

    # Semantic search — still goes directly through the repo
    results = store.working.search(
        query_embedding=[0.1, 0.2, ...],
        scope=scope,
        top_k=5,
    )

Step-4 lifecycle additions
--------------------------
``remember(record, scope)``
    Writes to the appropriate repository then calls the optional
    :class:`~agent_memory_sdk.types.Consolidator` synchronously.  When a
    consolidator is configured and the memory type is ``working`` or
    ``episodic``, the consolidator is invoked with the newly written record
    and any derived memories it returns are persisted via the appropriate
    repository.

``forget(record_id, memory_type, scope)``
    Facade-level tombstone.  Delegates to the correct repository's
    ``forget()`` method.  The ``memory_type`` argument accepts a string
    (``"working"``, ``"episodic"``, ``"facts"``, ``"profiles"``,
    ``"procedures"``) or the repository instance itself.

``purge_expired(scope)``
    Facade-level maintenance entry point.  Calls ``purge_expired(scope)``
    on every repository and returns a dict mapping table name → rows deleted.
    This method must be called explicitly (e.g. from a cron job); it is
    never invoked automatically.

Consolidator — sync vs. async
------------------------------
The default consolidator is :class:`~agent_memory_sdk.types.NoOpConsolidator`
which does nothing.  Supply a real implementation at construction time::

    store = MemoryStore(pool, consolidator=MyLLMConsolidator())

The consolidator runs **synchronously** on the ``remember()`` call path.
This is the simplest, lowest-dependency design: no background threads, no
queues, no external services required.

For production workloads where LLM calls are too slow to run inline,
run the consolidator out-of-band:

1. Leave ``consolidator`` as the default ``NoOpConsolidator`` so writes
   are fast.
2. Add a ``consolidated_at`` timestamp (or a ``"consolidated": false``
   flag) to each record's ``metadata`` at write time.
3. In a cron job or background worker, query for records where
   ``consolidated_at IS NULL`` (or ``metadata->'$.consolidated' = false``),
   call your consolidator, persist the derived memories, and mark the
   source rows as processed.

See ``scripts/consolidate_pending.py`` for a reference implementation of
the polling pattern.

See :class:`~agent_memory_sdk.types.Consolidator` for the full protocol
documentation and an LLM-based example.

PIPE-2 — ingest resolver (ADD/UPDATE/DELETE/NOOP at write time)
-----------------------------------------------------------------
The default is :class:`~agent_memory_sdk.types.NoOpIngestResolver`, which
always returns ``ADD`` — ``remember()`` inserts the candidate exactly as it
did before this feature existed, with no added overhead (the similarity
``search()`` call is skipped entirely when this default is in use).

Supply a real implementation at construction time to opt in::

    store = MemoryStore(pool, ingest_resolver=MyLLMIngestResolver(), resolver_k=5)

When an ``ingest_resolver`` is configured, ``remember()`` runs the following
*before* writing anything:

1. Embed the candidate (using its own ``embedding`` if already set, else the
   configured ``embedding_provider``).
2. ``search()`` the same-type repository, scoped to the candidate's scope,
   with ``top_k=resolver_k``, to find the most similar existing records.
3. Pair each result with its cosine distance to the candidate and call the
   configured :class:`~agent_memory_sdk.types.IngestResolver` with
   ``(candidate, similar)``.
4. Act on the returned :class:`~agent_memory_sdk.types.IngestDecision`:
   ``ADD`` inserts as today; ``UPDATE`` merges the candidate's content into
   the ``target_id`` row via the existing optimistic-concurrency ``update()``;
   ``DELETE`` calls ``forget()`` on ``target_id`` (the candidate itself is not
   written); ``NOOP`` skips the write entirely.

This is a **write-time** classifier over the top-k most-similar candidates by
cosine distance — distinct from :class:`~agent_memory_sdk.types.Reconciler`,
which runs later, in batches, over already-written non-superseded facts,
looking for contradictions *between* them. See
:class:`~agent_memory_sdk.types.IngestResolver` for the full protocol
documentation and an LLM-based example.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from agent_memory_sdk.exceptions import StaleWriteError as StaleWriteError  # re-export
from agent_memory_sdk.models import (
    EntityProfile,
    EpisodicMemory,
    MemoryScope,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
    _MemoryBase,
)
from agent_memory_sdk.repositories.chunks import ChunkRepository
from agent_memory_sdk.repositories.episodic import EpisodicMemoryRepository
from agent_memory_sdk.repositories.facts import SemanticFactRepository
from agent_memory_sdk.repositories.procedural import ProceduralMemoryRepository
from agent_memory_sdk.repositories.profiles import EntityProfileRepository
from agent_memory_sdk.repositories.working import WorkingMemoryRepository
from agent_memory_sdk.types import (
    ContextCard,
    IngestAction,
    IngestDecision,
    NoOpConsolidator,
    NoOpIngestResolver,
    NoOpReconciler,
    NoOpSummarizer,
)

logger = logging.getLogger(__name__)


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """Return ``1 - cosine_similarity(a, b)``, matching Db2's ``VECTOR_DISTANCE``
    ``COSINE`` semantics (see :class:`~agent_memory_sdk.types.DistanceMetric`).

    This is computed in pure Python because :meth:`BaseRepository.search`
    (PIPE-2's ``similar`` results) returns typed model instances, not
    distances — both the candidate's and each result's ``embedding`` field
    are already available in Python, so no extra SQL round-trip is needed.

    Returns ``1.0`` (maximum distance) when either vector is empty, the
    vectors have mismatched dimensions, or either vector has zero norm (e.g.
    the ORC-2 zero-vector chunking sentinel) — cosine similarity is undefined
    in those cases, and treating them as maximally dissimilar is the safe
    default for a classifier deciding whether to merge/replace a record.
    """
    if not a or not b or len(a) != len(b):
        return 1.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    similarity = dot / (norm_a * norm_b)
    # Clamp for floating-point rounding just outside [-1, 1].
    similarity = max(-1.0, min(1.0, similarity))
    return 1.0 - similarity


# Map from memory model class → repository attribute name
_MODEL_TO_REPO_ATTR: dict[type, str] = {
    WorkingMemory: "working",
    EpisodicMemory: "episodic",
    SemanticFact: "facts",
    EntityProfile: "profiles",
    ProceduralMemory: "procedures",
}

# String aliases accepted by forget() / purge_expired()
_ALIAS_TO_ATTR: dict[str, str] = {
    "working": "working",
    "episodic": "episodic",
    "facts": "facts",
    "semantic_facts": "facts",
    "profiles": "profiles",
    "entity_profiles": "profiles",
    "procedures": "procedures",
    "procedural": "procedures",
}


class MemoryStore:
    """Composition root for all five memory-type repositories.

    Attributes:
        working:    :class:`~agent_memory_sdk.repositories.WorkingMemoryRepository`
        episodic:   :class:`~agent_memory_sdk.repositories.EpisodicMemoryRepository`
        facts:      :class:`~agent_memory_sdk.repositories.SemanticFactRepository`
        profiles:   :class:`~agent_memory_sdk.repositories.EntityProfileRepository`
        procedures: :class:`~agent_memory_sdk.repositories.ProceduralMemoryRepository`
        chunks:     :class:`~agent_memory_sdk.repositories.chunks.ChunkRepository`
                    (ORC-2 — only populated when *enable_chunking* is True)

    Args:
        pool: A :class:`~agent_memory_sdk.db.connection.ConnectionPool`
              instance, or any object whose ``get_connection()`` context
              manager yields a DB-API 2.0 connection.
        embedding_dim: The vector dimension used by all tables (default
              1536, matching the DDL default in 0002_memory_tables.sql).
              Override if you change the schema to a different model.
        embedding_provider: An :class:`~agent_memory_sdk.types.EmbeddingProvider`
              callable (``text -> list[float]``).  When provided, it is
              injected into every repository and used to produce per-chunk
              embeddings for long content (ORC-2 chunking).  Also used by
              the default ``recall()`` path for on-the-fly query embedding.
              Defaults to ``None`` — no chunking, callers embed manually.
        consolidator: A :class:`~agent_memory_sdk.types.Consolidator`
              implementation (any callable matching the protocol).  Called
              synchronously after every ``remember()`` write to
              ``working`` or ``episodic`` memory.  Defaults to
              :class:`~agent_memory_sdk.types.NoOpConsolidator` (does
              nothing).
        reconciler: A :class:`~agent_memory_sdk.types.Reconciler`
              implementation (any callable matching the protocol).  Used by
              :meth:`reconcile` to detect contradictions among live semantic
              facts.  Defaults to
              :class:`~agent_memory_sdk.types.NoOpReconciler` (does nothing).
        summarizer: A :class:`~agent_memory_sdk.types.Summarizer`
              implementation (any callable matching the protocol).  Called by
              :meth:`get_context_card` to produce an optional condensed
              narrative from the assembled turns.  Defaults to
              :class:`~agent_memory_sdk.types.NoOpSummarizer` — when the
              default is in use, :meth:`get_context_card` sets
              ``ContextCard.summary`` to ``None`` (no LLM call, no overhead).
        consolidate_every_n: Throttle the inline synchronous consolidator
              so it only fires every *N*-th ``remember()`` call for
              working/episodic writes **per scope** (keyed by
              ``(agent_id, user_id, thread_id)``).  Default is ``1``
              (fire on every write — existing behaviour).  Set to a value
              greater than 1 to reduce LLM-call cost on hot write paths::

                  # Consolidate every 5th turn per scope
                  store = MemoryStore(pool, consolidator=llm_consolidator,
                                      consolidate_every_n=5)

              **Known limitation:** the per-scope counter is stored in-memory
              on the ``MemoryStore`` instance.  It resets to zero on process
              restart and is **not shared across multiple application
              instances** (e.g. multiple gunicorn workers or Kubernetes
              replicas) — each process maintains its own independent counter.
              This means that with N workers and ``consolidate_every_n=5``,
              each worker consolidates every 5th write it handles personally,
              not globally every 5th write across all workers.  For
              cross-process cadence, use the background worker
              (``scripts/consolidate_pending.py``) instead of the inline
              consolidator.  This limitation is recorded in
              project-management/DECISIONS.md ENH-4 entry.
        enable_chunking: When True (default) and *embedding_provider* is
              supplied, activate ORC-2 content chunking: records whose
              content exceeds *chunk_threshold* are split into overlapping
              chunks and embedded separately in ``memory_chunks``.  When
              False, chunking is disabled regardless of *embedding_provider*.
        chunk_threshold: Content-length threshold in characters above which
              chunking is applied.  Default 2000.
        chunk_size:    Maximum characters per chunk.  Default 800.
        chunk_overlap: Overlap in characters between adjacent chunks.
              Default 200.
        ingest_resolver: An :class:`~agent_memory_sdk.types.IngestResolver`
              implementation (any callable matching the protocol).  When
              configured, :meth:`remember` runs a similarity ``search()``
              (``top_k=resolver_k``) against the same-type repository before
              writing, and acts on the returned
              :class:`~agent_memory_sdk.types.IngestDecision` (``ADD`` /
              ``UPDATE`` / ``DELETE`` / ``NOOP``) instead of unconditionally
              inserting.  Defaults to
              :class:`~agent_memory_sdk.types.NoOpIngestResolver`, which
              always returns ``ADD`` — with this default, ``remember()``
              skips the similarity search entirely and behaves exactly as it
              did before PIPE-2 (zero added overhead).
        resolver_k: ``top_k`` passed to the similarity ``search()`` run
              before the configured ``ingest_resolver`` is invoked.  Ignored
              when ``ingest_resolver`` is not configured.  Default ``5``.
    """

    def __init__(
        self,
        pool: Any,
        embedding_dim: int = 1536,
        embedding_provider: Any | None = None,
        consolidator: Any | None = None,
        reconciler: Any | None = None,
        summarizer: Any | None = None,
        consolidate_every_n: int = 1,
        enable_chunking: bool = True,
        chunk_threshold: int = 2000,
        chunk_size: int = 800,
        chunk_overlap: int = 200,
        ingest_resolver: Any | None = None,
        resolver_k: int = 5,
    ) -> None:
        # Build the shared ChunkRepository when chunking is enabled and
        # an embedding provider has been supplied — otherwise it stays None
        # and the per-type repos fall back to the pre-ORC-2 single-embedding
        # path automatically.
        chunk_repo: ChunkRepository | None = None
        if enable_chunking and embedding_provider is not None:
            chunk_repo = ChunkRepository(pool, embedding_dim=embedding_dim)

        self.working = WorkingMemoryRepository(
            pool,
            chunk_repo=chunk_repo,
            chunk_threshold=chunk_threshold,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self.episodic = EpisodicMemoryRepository(
            pool,
            chunk_repo=chunk_repo,
            chunk_threshold=chunk_threshold,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self.facts = SemanticFactRepository(
            pool,
            chunk_repo=chunk_repo,
            chunk_threshold=chunk_threshold,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self.profiles = EntityProfileRepository(
            pool,
            chunk_repo=chunk_repo,
            chunk_threshold=chunk_threshold,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self.procedures = ProceduralMemoryRepository(
            pool,
            chunk_repo=chunk_repo,
            chunk_threshold=chunk_threshold,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        # Expose the chunk repo as a public attribute so callers can use it
        # directly (e.g. maintenance scripts, integration tests).
        self.chunks: ChunkRepository | None = chunk_repo

        # Propagate the embedding dimension AND embedding provider to all repos.
        for repo in (
            self.working,
            self.episodic,
            self.facts,
            self.profiles,
            self.procedures,
        ):
            repo.EMBEDDING_DIM = embedding_dim
            repo._embedding_provider = embedding_provider

        self._consolidator = consolidator if consolidator is not None else NoOpConsolidator()
        self._reconciler = reconciler if reconciler is not None else NoOpReconciler()
        self._summarizer = summarizer if summarizer is not None else NoOpSummarizer()
        self._ingest_resolver = (
            ingest_resolver if ingest_resolver is not None else NoOpIngestResolver()
        )

        if consolidate_every_n < 1:
            raise ValueError(
                f"consolidate_every_n must be >= 1; got {consolidate_every_n!r}."
            )
        self._consolidate_every_n: int = consolidate_every_n
        # Per-scope call counter (key: (agent_id, user_id, thread_id) tuple).
        # In-memory; resets on process restart and is not shared across
        # multiple app instances — see the class docstring for the implication.
        self._consolidate_counters: dict[tuple[str | None, ...], int] = {}

        if resolver_k < 1:
            raise ValueError(f"resolver_k must be >= 1; got {resolver_k!r}.")
        self._resolver_k: int = resolver_k

    # ------------------------------------------------------------------
    # remember() — primary write entry point
    # ------------------------------------------------------------------

    def remember(self, record: _MemoryBase, scope: MemoryScope) -> _MemoryBase:
        """Write a memory record and optionally run ingest resolution + consolidation.

        Determines the target repository from the record's type.

        **PIPE-2 — ingest resolution:** when a real ``ingest_resolver`` is
        configured (anything other than the default
        :class:`~agent_memory_sdk.types.NoOpIngestResolver`), this method
        first runs a similarity ``search()`` against the same-type repository
        (``top_k=resolver_k``) and calls the resolver with the candidate plus
        those results before deciding whether to insert, merge, discard, or
        skip the write — see :meth:`_resolve_and_act` and
        :class:`~agent_memory_sdk.types.IngestResolver` for the full flow.
        With the default resolver, this step is skipped entirely and
        ``create()`` is called unconditionally — byte-for-byte the pre-PIPE-2
        behavior.

        Then — if the memory type is ``working`` or ``episodic`` and a new
        row was actually inserted (an ``ADD`` decision, or the default
        always-ADD path) — calls the configured :attr:`consolidator`
        synchronously with the written record.

        Any derived memories returned by the consolidator are persisted
        immediately via the appropriate repository (facts, profiles, or
        procedures) with the same *scope*.

        Args:
            record: A model instance (WorkingMemory, EpisodicMemory,
                    SemanticFact, EntityProfile, or ProceduralMemory).
            scope:  Must include at minimum agent_id.

        Returns:
            The persisted record for ``ADD``/``UPDATE`` decisions (the newly
            created row, or the merged-into target row, respectively, each
            with ``id``/``version``/timestamps as returned by the
            repository).  For ``DELETE``/``NOOP`` decisions — where the
            candidate itself is never written — returns the candidate
            *as passed in*, unmodified and unpersisted.

        Raises:
            TypeError:    if ``record`` is not one of the five known model types.
            ValueError:   if scope.agent_id is missing.
        """
        repo_attr = _MODEL_TO_REPO_ATTR.get(type(record))
        if repo_attr is None:
            raise TypeError(
                f"Unknown memory type: {type(record).__name__}. "
                "Expected one of: WorkingMemory, EpisodicMemory, SemanticFact, "
                "EntityProfile, ProceduralMemory."
            )

        repo = getattr(self, repo_attr)

        if isinstance(self._ingest_resolver, NoOpIngestResolver):
            # Fast path — unchanged pre-PIPE-2 behavior, no similarity search.
            stored: _MemoryBase = repo.create(record, scope)
            did_add = True
        else:
            stored, did_add = self._resolve_and_act(repo, record, scope)

        # Run consolidation only for working / episodic writes where a new
        # row was actually inserted (ADD).
        if did_add and repo_attr in ("working", "episodic") and self._should_consolidate(scope):
            self._run_consolidator([stored], scope)

        return stored

    def _resolve_and_act(
        self, repo: Any, record: _MemoryBase, scope: MemoryScope
    ) -> tuple[_MemoryBase, bool]:
        """Run the configured IngestResolver and act on its decision (PIPE-2).

        Args:
            repo:   The type repository resolved from ``record``'s type
                    (``self.working``, ``self.facts``, etc.).
            record: The candidate record about to be remembered.
            scope:  Must include at minimum agent_id.

        Returns:
            ``(stored_record, did_add)`` — ``did_add`` is True only when a
            new row was actually inserted (``ADD``, including the fallbacks
            below), so :meth:`remember` knows whether to also run the
            consolidator.
        """
        candidate_embedding = self._candidate_embedding(repo, record)

        similar: list[tuple[_MemoryBase, float]] = []
        if candidate_embedding:
            try:
                neighbors = repo.search(
                    candidate_embedding, scope, top_k=self._resolver_k
                )
                similar = [
                    (n, _cosine_distance(candidate_embedding, n.embedding))
                    for n in neighbors
                ]
            except Exception:
                logger.exception(
                    "IngestResolver: search() for similar records raised; "
                    "proceeding with an empty similar-records list."
                )
                similar = []

        try:
            decision = self._ingest_resolver(record, similar)
        except Exception:
            logger.exception(
                "IngestResolver raised an exception; falling back to ADD."
            )
            decision = IngestDecision(action=IngestAction.ADD)

        if decision.action == IngestAction.ADD:
            return repo.create(record, scope), True

        if decision.action == IngestAction.UPDATE:
            if not decision.target_id:
                logger.warning(
                    "IngestResolver returned UPDATE with no target_id; falling back to ADD."
                )
                return repo.create(record, scope), True
            target = repo.get_by_id(decision.target_id, scope)
            if target is None:
                logger.warning(
                    "IngestResolver UPDATE target_id=%s not found (wrong scope or "
                    "already deleted); falling back to ADD.",
                    decision.target_id,
                )
                return repo.create(record, scope), True
            target.content = record.content
            target.metadata = record.metadata
            target.embedding = record.embedding
            target.confidence = record.confidence
            updated: _MemoryBase = repo.update(target, scope)
            return updated, False

        if decision.action == IngestAction.DELETE:
            if decision.target_id:
                ok = repo.forget(decision.target_id, scope)
                if not ok:
                    logger.warning(
                        "IngestResolver DELETE target_id=%s not found or already "
                        "deleted; nothing forgotten.",
                        decision.target_id,
                    )
            else:
                logger.warning(
                    "IngestResolver returned DELETE with no target_id; nothing forgotten."
                )
            return record, False

        # NOOP (or any unrecognized action — treated conservatively as NOOP):
        # the candidate is not written.
        return record, False

    def _candidate_embedding(self, repo: Any, record: _MemoryBase) -> list[float]:
        """Return the embedding to use for the PIPE-2 similarity search.

        Uses ``record.embedding`` if the caller already set it; otherwise
        computes one via the repository's configured ``embedding_provider``
        (the same provider wired in via ``MemoryStore(embedding_provider=…)``).
        Returns ``[]`` (no similarity search will be run) when neither is
        available, or if the provider raises.
        """
        if record.embedding:
            return record.embedding
        provider = getattr(repo, "_embedding_provider", None)
        if provider is None:
            return []
        try:
            embedding: list[float] = provider(record.content)
            return embedding
        except Exception:
            logger.exception(
                "IngestResolver: embedding_provider raised while embedding the "
                "candidate; skipping similarity search for this write."
            )
            return []

    def _should_consolidate(self, scope: MemoryScope) -> bool:
        """Return True if the inline consolidator should fire for this write.

        Implements the ``consolidate_every_n`` throttle.  The counter is
        keyed by ``(agent_id, user_id, thread_id)`` so each distinct
        conversational scope gets its own independent cadence — a burst of
        writes from one user does not delay consolidation for another user
        in a multi-user deployment.

        When ``consolidate_every_n == 1`` (the default), this always returns
        True with no dict lookup overhead.
        """
        if self._consolidate_every_n == 1:
            return True
        key = (scope.agent_id, scope.user_id, scope.thread_id)
        count = self._consolidate_counters.get(key, 0) + 1
        self._consolidate_counters[key] = count
        if count >= self._consolidate_every_n:
            self._consolidate_counters[key] = 0
            return True
        return False

    def _run_consolidator(
        self, raw_memories: list[_MemoryBase], scope: MemoryScope
    ) -> None:
        """Invoke the consolidator and persist any derived records.

        Errors in the consolidator are logged but do not bubble up —
        a consolidation failure must not roll back the original write.
        Callers who need strict guarantees should wrap ``remember()``
        themselves.
        """
        try:
            derived = self._consolidator(raw_memories)
        except Exception:
            logger.exception(
                "Consolidator raised an exception; derived memories not written."
            )
            return

        for derived_record in derived:
            repo_attr = _MODEL_TO_REPO_ATTR.get(type(derived_record))
            if repo_attr is None:
                logger.warning(
                    "Consolidator returned unknown type %s; skipping.",
                    type(derived_record).__name__,
                )
                continue
            try:
                getattr(self, repo_attr).create(derived_record, scope)
                logger.debug(
                    "Persisted derived %s id=%s from consolidator.",
                    type(derived_record).__name__,
                    derived_record.id,
                )
            except Exception:
                logger.exception(
                    "Failed to persist derived %s from consolidator.",
                    type(derived_record).__name__,
                )

    # ------------------------------------------------------------------
    # forget() — tombstone a row
    # ------------------------------------------------------------------

    def forget(
        self,
        record_id: str,
        memory_type: str,
        scope: MemoryScope,
    ) -> bool:
        """Tombstone a row without hard-deleting it.

        This is the facade-level entry point for ``forget``.  For
        per-repository soft-delete, use ``store.<repo>.forget(id, scope)``
        directly.

        Args:
            record_id:   UUID of the row to tombstone.
            memory_type: One of ``"working"``, ``"episodic"``, ``"facts"``
                         (or ``"semantic_facts"``), ``"profiles"``
                         (or ``"entity_profiles"``), ``"procedures"``
                         (or ``"procedural"``).
            scope:       Must include at minimum agent_id.

        Returns:
            True if the row was found and tombstoned; False if not found.

        Raises:
            ValueError: if ``memory_type`` is not a recognised alias, or
                        if scope.agent_id is missing.
        """
        repo = self._resolve_repo(memory_type)
        result: bool = repo.forget(record_id, scope)
        return result

    # ------------------------------------------------------------------
    # purge_expired() — maintenance hard-delete
    # ------------------------------------------------------------------

    def purge_expired(self, scope: MemoryScope) -> dict[str, int]:
        """Hard-delete tombstoned rows across all five tables.

        Calls ``purge_expired(scope)`` on every repository.  The scope is
        required so that cross-tenant/agent data is never touched.

        This method must be called explicitly (e.g. from a cron job or the
        ``scripts/purge_expired.py`` script).  It is **never** invoked
        automatically.

        Args:
            scope: Must include at minimum agent_id.

        Returns:
            A dict mapping table name to the number of rows deleted, e.g.::

                {
                    "working_memory": 12,
                    "episodic_memory": 3,
                    "semantic_facts": 0,
                    "entity_profiles": 0,
                    "procedural_memory": 1,
                }
        """
        results: dict[str, int] = {}
        for repo in (
            self.working,
            self.episodic,
            self.facts,
            self.profiles,
            self.procedures,
        ):
            results[repo._TABLE] = repo.purge_expired(scope)
        return results

    # ------------------------------------------------------------------
    # reconcile() — run a reconciliation pass over semantic facts
    # ------------------------------------------------------------------

    def reconcile(
        self,
        memory_type: str,
        scope: MemoryScope,
        limit: int = 200,
    ) -> list[Any]:
        """Fetch live facts and run the configured Reconciler to detect contradictions.

        Fetches the most recent non-deleted, non-superseded records for the
        given *memory_type* and *scope*, invokes the configured
        :class:`~agent_memory_sdk.types.Reconciler`, and for each returned
        :class:`~agent_memory_sdk.types.SupersedeDecision` calls
        :meth:`~agent_memory_sdk.repositories.facts.SemanticFactRepository.supersede`
        on the loser row.

        **Soft-supersession vs. forget():**
        This method uses a distinct mechanism from
        :meth:`forget`: ``superseded_at`` is set (not ``deleted_at``), so
        the audit trail can distinguish "the AI decided this was contradicted"
        from "the user/operator asked us to forget this."

        Currently only ``"facts"`` / ``"semantic_facts"`` is supported — those
        are the only rows that carry the supersession columns.  Passing any
        other memory type raises :exc:`ValueError`.

        Args:
            memory_type: Must be ``"facts"`` or ``"semantic_facts"``.
            scope:       Must include at minimum agent_id.
            limit:       How many recent live facts to fetch as candidates for
                         the reconciliation pass (default 200, capped at 1000).

        Returns:
            The list of :class:`~agent_memory_sdk.types.SupersedeDecision`
            objects returned by the Reconciler.  Decisions that did not match
            a live row (e.g. the loser was already superseded by a concurrent
            call) are silently skipped.

        Raises:
            ValueError: if ``memory_type`` is not ``"facts"`` /
                        ``"semantic_facts"``, or if scope.agent_id is missing.
        """
        # Only semantic_facts carries supersession columns.
        if memory_type not in ("facts", "semantic_facts"):
            raise ValueError(
                f"reconcile() only supports memory_type='facts' / 'semantic_facts'; "
                f"got {memory_type!r}.  entity_profiles and procedural_memory do not "
                f"carry supersession columns (see project-management/DECISIONS.md ENH-3 entry)."
            )

        candidates = self.facts.list_all(scope, limit=min(limit, 1000))

        try:
            decisions = self._reconciler(candidates)
        except Exception:
            logger.exception(
                "Reconciler raised an exception; no supersession decisions applied."
            )
            return []

        # Build a set of candidate IDs once for O(1) membership checks below.
        candidate_ids: set[str] = {c.id for c in candidates}

        applied: list[Any] = []
        for decision in decisions:
            # Guard (a): self-supersession — a fact cannot supersede itself.
            if decision.winner_id == decision.loser_id:
                logger.warning(
                    "reconcile: skipping self-supersession decision "
                    "winner_id == loser_id == %s; reason=%r",
                    decision.winner_id,
                    decision.reason,
                )
                continue

            # Guard (b): winner must be a known live candidate from the batch
            # that was handed to the Reconciler.  An id not in that set means
            # the Reconciler hallucinated a reference (or returned a stale/
            # cross-scope id), which would corrupt the audit trail.
            if decision.winner_id not in candidate_ids:
                logger.warning(
                    "reconcile: skipping decision whose winner_id=%s is not "
                    "among the %d candidates passed to the Reconciler "
                    "(loser_id=%s, reason=%r)",
                    decision.winner_id,
                    len(candidate_ids),
                    decision.loser_id,
                    decision.reason,
                )
                continue

            try:
                ok = self.facts.supersede(
                    loser_id=decision.loser_id,
                    winner_id=decision.winner_id,
                    reason=decision.reason,
                    scope=scope,
                )
                if ok:
                    applied.append(decision)
                    logger.debug(
                        "reconcile: superseded fact loser=%s winner=%s reason=%r",
                        decision.loser_id,
                        decision.winner_id,
                        decision.reason,
                    )
                else:
                    logger.debug(
                        "reconcile: supersede no-op (already superseded or not found) "
                        "loser=%s winner=%s",
                        decision.loser_id,
                        decision.winner_id,
                    )
            except Exception:
                logger.exception(
                    "reconcile: failed to supersede loser=%s winner=%s",
                    decision.loser_id,
                    decision.winner_id,
                )
        return applied

    # ------------------------------------------------------------------
    # get_context_card() — structured recent-turns view (ORC-1)
    # ------------------------------------------------------------------

    def get_context_card(
        self,
        scope: MemoryScope,
        max_turns: int = 20,
    ) -> ContextCard:
        """Return a structured view of recent working-memory turns for the active thread.

        Fetches up to *max_turns* working-memory records for *scope* in
        reverse-chronological order (newest first, per the default
        ``list_all()`` ordering), then reverses the list to produce the
        chronological (oldest-first) view expected by a context window.

        No new schema is required — this is a convenience layer over
        :meth:`~agent_memory_sdk.repositories.WorkingMemoryRepository.list_all`.

        If a :class:`~agent_memory_sdk.types.Summarizer` was supplied at
        construction time, it is called on the turns list (chronological order)
        and its output is placed in :attr:`~agent_memory_sdk.types.ContextCard.summary`.
        With the default :class:`~agent_memory_sdk.types.NoOpSummarizer`, the
        summary is ``None``.

        Summarizer errors are logged and do not propagate — the card is
        returned with ``summary=None`` on failure, so the caller always
        receives a valid card even if the LLM is unavailable.

        Args:
            scope:     Must include at minimum ``agent_id``.  If
                       ``thread_id`` is set on the scope, only turns for
                       that thread are returned — this is the typical usage
                       for a single active conversation.  Passing a scope
                       without ``thread_id`` returns all working-memory turns
                       for the agent (useful for cross-thread summaries).
            max_turns: Maximum number of turns to include (default 20).
                       Must be >= 1.

        Returns:
            A :class:`~agent_memory_sdk.types.ContextCard` with:

            * ``turns`` — list of :class:`~agent_memory_sdk.models.WorkingMemory`
              records in chronological order.
            * ``turn_count`` — ``len(turns)``.
            * ``latest_at`` — ``created_at`` of the newest turn, or ``None``
              if there are no turns.
            * ``summary`` — narrative string from the :class:`~agent_memory_sdk.types.Summarizer`,
              or ``None`` if no summarizer is configured.

        Raises:
            ValueError: if ``max_turns < 1`` or ``scope.agent_id`` is missing.
        """
        if max_turns < 1:
            raise ValueError(f"max_turns must be >= 1; got {max_turns!r}.")

        # list_all() returns newest-first by default; cap at max_turns and
        # reverse to get chronological order for context window consumption.
        recent = self.working.list_all(scope, limit=max_turns)
        turns = list(reversed(recent))

        latest_at = recent[0].created_at if recent else None

        summary: str | None = None
        if not isinstance(self._summarizer, NoOpSummarizer):
            try:
                result = self._summarizer(turns)
                summary = result if result else None
            except Exception:
                logger.exception(
                    "Summarizer raised an exception; ContextCard.summary set to None."
                )

        return ContextCard(
            turns=turns,
            turn_count=len(turns),
            latest_at=latest_at,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_repo(self, memory_type: str) -> Any:
        attr = _ALIAS_TO_ATTR.get(memory_type)
        if attr is None:
            raise ValueError(
                f"Unknown memory_type {memory_type!r}. "
                f"Expected one of: {', '.join(sorted(_ALIAS_TO_ATTR))}."
            )
        return getattr(self, attr)

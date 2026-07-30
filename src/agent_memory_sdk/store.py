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
"""

from __future__ import annotations

import logging
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
from agent_memory_sdk.repositories.episodic import EpisodicMemoryRepository
from agent_memory_sdk.repositories.facts import SemanticFactRepository
from agent_memory_sdk.repositories.procedural import ProceduralMemoryRepository
from agent_memory_sdk.repositories.profiles import EntityProfileRepository
from agent_memory_sdk.repositories.working import WorkingMemoryRepository
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler

logger = logging.getLogger(__name__)

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

    Args:
        pool: A :class:`~agent_memory_sdk.db.connection.ConnectionPool`
              instance, or any object whose ``get_connection()`` context
              manager yields a DB-API 2.0 connection.
        embedding_dim: The vector dimension used by all tables (default
              1536, matching the DDL default in 0002_memory_tables.sql).
              Override if you change the schema to a different model.
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
              consolidator.  This limitation is recorded in DECISIONS.md
              ENH-4 entry.
    """

    def __init__(
        self,
        pool: Any,
        embedding_dim: int = 1536,
        consolidator: Any | None = None,
        reconciler: Any | None = None,
        consolidate_every_n: int = 1,
    ) -> None:
        self.working = WorkingMemoryRepository(pool)
        self.episodic = EpisodicMemoryRepository(pool)
        self.facts = SemanticFactRepository(pool)
        self.profiles = EntityProfileRepository(pool)
        self.procedures = ProceduralMemoryRepository(pool)

        # Propagate the embedding dimension to all repos so they can
        # produce correctly-dimensioned zero-vector sentinels.
        for repo in (
            self.working,
            self.episodic,
            self.facts,
            self.profiles,
            self.procedures,
        ):
            repo.EMBEDDING_DIM = embedding_dim

        self._consolidator = consolidator if consolidator is not None else NoOpConsolidator()
        self._reconciler = reconciler if reconciler is not None else NoOpReconciler()

        if consolidate_every_n < 1:
            raise ValueError(
                f"consolidate_every_n must be >= 1; got {consolidate_every_n!r}."
            )
        self._consolidate_every_n: int = consolidate_every_n
        # Per-scope call counter (key: (agent_id, user_id, thread_id) tuple).
        # In-memory; resets on process restart and is not shared across
        # multiple app instances — see the class docstring for the implication.
        self._consolidate_counters: dict[tuple[str | None, ...], int] = {}

    # ------------------------------------------------------------------
    # remember() — primary write entry point
    # ------------------------------------------------------------------

    def remember(self, record: _MemoryBase, scope: MemoryScope) -> _MemoryBase:
        """Write a memory record and optionally run consolidation.

        Determines the target repository from the record's type, calls
        ``create()``, then — if the memory type is ``working`` or
        ``episodic`` — calls the configured
        :attr:`consolidator` synchronously with the written record.

        Any derived memories returned by the consolidator are persisted
        immediately via the appropriate repository (facts, profiles, or
        procedures) with the same *scope*.

        Args:
            record: A model instance (WorkingMemory, EpisodicMemory,
                    SemanticFact, EntityProfile, or ProceduralMemory).
            scope:  Must include at minimum agent_id.

        Returns:
            The persisted record (same object, with ``id``, ``created_at``,
            etc. populated by the repository).

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
        stored: _MemoryBase = repo.create(record, scope)

        # Run consolidation only for working / episodic writes.
        if repo_attr in ("working", "episodic") and self._should_consolidate(scope):
            self._run_consolidator([stored], scope)

        return stored

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
                f"carry supersession columns (see DECISIONS.md ENH-3 entry)."
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

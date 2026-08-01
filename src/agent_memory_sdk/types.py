"""
types.py
~~~~~~~~
Shared protocol and enum types used across the SDK.

These definitions are dependency-light (stdlib + enum only) and are
imported by both models.py and the repository layer, so they live in
their own module to avoid circular imports.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agent_memory_sdk.models import EntityProfile, SemanticFact, WorkingMemory, _MemoryBase

# ---------------------------------------------------------------------------
# EmbeddingProvider
# ---------------------------------------------------------------------------

@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for anything that turns text into a vector.

    The SDK never imports a specific embedding model. Callers inject their
    own provider — an OpenAI client wrapper, a sentence-transformers model,
    a stub for tests, etc.

    The returned list must have exactly the dimension that matches the
    VECTOR column in the target table (default: 1536).  The SDK does NOT
    validate the length; it passes the vector straight to Db2.

    Example::

        class OpenAIEmbedder:
            def __call__(self, text: str) -> list[float]:
                response = openai.embeddings.create(
                    model="text-embedding-3-small", input=text
                )
                return response.data[0].embedding

        store = MemoryStore(pool, embedding_provider=OpenAIEmbedder())
    """

    def __call__(self, text: str) -> list[float]:
        """Embed *text* and return a list of floats.

        Args:
            text: The plain-text string to embed.

        Returns:
            A list of float coordinates (the embedding vector).
        """
        ...


# ---------------------------------------------------------------------------
# Consolidator
# ---------------------------------------------------------------------------

class Consolidator(Protocol):
    """Protocol for pluggable memory consolidation callbacks.

    A ``Consolidator`` is called synchronously by :class:`MemoryStore` after
    a write to **working** or **episodic** memory.  It receives the raw
    memories just written and returns zero or more *derived* memory objects
    (semantic facts, entity-profile updates, procedural memories) that the
    store will persist.

    Shape::

        (raw_memories: list[_MemoryBase]) -> list[_MemoryBase]

    The returned list may contain any mix of
    :class:`~agent_memory_sdk.models.SemanticFact`,
    :class:`~agent_memory_sdk.models.EntityProfile`, and
    :class:`~agent_memory_sdk.models.ProceduralMemory` instances.  The
    caller is responsible for setting ``agent_id`` (and any other scope
    fields) on the returned records; the store passes each one straight to
    the appropriate repository's ``create()`` method with the same scope
    that was used for the triggering write.

    **Sync path (default)**
    -----------------------
    Pass a ``Consolidator`` to :class:`MemoryStore` at construction time::

        store = MemoryStore(pool, consolidator=MyLLMConsolidator())

    The consolidator is called inline, blocking the current thread until it
    returns.  This is simple and correct for low-latency or test workloads,
    but will block the agent's hot path if the consolidator makes slow LLM
    calls.

    **Async / background path (extension point)**
    -----------------------------------------------
    For production workloads, run the consolidator out-of-band:

    1. **Leave the sync consolidator as the no-op default** (or omit it
       entirely) so the write path is fast.
    2. Add a ``consolidated_at`` field (or a boolean flag in ``metadata``) to
       rows that need processing.
    3. In a cron job or background worker, query for
       ``consolidated_at IS NULL`` rows, call your ``Consolidator``
       implementation, persist the derived records, and mark the source rows
       as consolidated.

    See ``scripts/consolidate_pending.py`` for a reference implementation of
    this async polling pattern.

    **LLM-based consolidator example**
    ------------------------------------
    ::

        import openai
        from agent_memory_sdk.models import SemanticFact
        from agent_memory_sdk.types import Consolidator

        class LLMConsolidator:
            \"\"\"Extract atomic facts from raw working/episodic memories.\"\"\"

            def __init__(self, client: openai.OpenAI, agent_id: str) -> None:
                self._client = client
                self._agent_id = agent_id

            def __call__(
                self, raw_memories: list
            ) -> list:
                if not raw_memories:
                    return []

                combined = "\\n".join(m.content for m in raw_memories)
                resp = self._client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Extract atomic facts from the conversation below. "
                                "Return one fact per line, nothing else."
                            ),
                        },
                        {"role": "user", "content": combined},
                    ],
                )
                facts_text = resp.choices[0].message.content or ""
                lines = [l.strip() for l in facts_text.splitlines() if l.strip()]
                facts = []
                for line in lines:
                    # Prefix "TENTATIVE:" signals lower certainty; any other
                    # line is treated as a confident, explicit fact.
                    is_tentative = line.upper().startswith("TENTATIVE:")
                    facts.append(
                        SemanticFact(
                            agent_id=self._agent_id,
                            content=line.removeprefix("TENTATIVE:").strip(),
                            # confidence reflects grounding certainty:
                            #   0.6 → LLM inferred this tentatively
                            #   0.95 → user stated this explicitly
                            confidence=0.6 if is_tentative else 0.95,
                            metadata={"source": "llm_consolidator"},
                        )
                    )
                return facts

        # Wire in at store construction:
        store = MemoryStore(
            pool,
            consolidator=LLMConsolidator(openai.OpenAI(), agent_id="agent-001"),
        )

    The above is a **synchronous** example.  For the async/background
    variant, see the docstring above and ``scripts/consolidate_pending.py``.
    """

    def __call__(self, raw_memories: list[_MemoryBase]) -> list[_MemoryBase]:
        """Consolidate raw memories into derived memory records.

        Args:
            raw_memories: The memories just written to working or episodic
                memory.  Each element is a fully-populated model instance
                with ``id``, ``agent_id``, ``content``, etc. set.

        Returns:
            A (possibly empty) list of derived memory objects.  May contain
            any mix of :class:`~agent_memory_sdk.models.SemanticFact`,
            :class:`~agent_memory_sdk.models.EntityProfile`, and
            :class:`~agent_memory_sdk.models.ProceduralMemory` instances.
            Return ``[]`` to produce no derived memories.
        """
        ...


class NoOpConsolidator:
    """Default consolidator that does nothing.

    Returned derived list is always empty.  This is the default used by
    :class:`MemoryStore` when no ``consolidator`` argument is supplied —
    callers opt in to consolidation explicitly.

    Because it returns an empty list, the store skips all derived-memory
    writes, making writes identical in cost to Step 3 behaviour.
    """

    def __call__(self, raw_memories: list[_MemoryBase]) -> list[_MemoryBase]:
        return []


# ---------------------------------------------------------------------------
# SupersedeDecision, Reconciler, NoOpReconciler
# ---------------------------------------------------------------------------

@dataclass
class SupersedeDecision:
    """A single reconciliation decision produced by a :class:`Reconciler`.

    Attributes:
        winner_id:  ``id`` of the fact that wins (the more current / correct
                    record).  This row is left untouched.
        loser_id:   ``id`` of the fact that is superseded (the stale /
                    contradicted record).  Its ``superseded_by``,
                    ``superseded_at``, and ``supersede_reason`` fields will be
                    set by :meth:`~agent_memory_sdk.store.MemoryStore.reconcile`.
        reason:     Human-readable explanation, e.g.
                    ``"contradicts: user now prefers light mode"``.
    """

    winner_id: str
    loser_id: str
    reason: str


class Reconciler(Protocol):
    """Protocol for pluggable memory reconciliation callbacks.

    A ``Reconciler`` examines a list of candidate :class:`~agent_memory_sdk.models.SemanticFact`
    records and returns zero or more :class:`SupersedeDecision` objects
    indicating which facts should be soft-superseded.

    Shape::

        (candidates: list[SemanticFact]) -> list[SupersedeDecision]

    This protocol is parallel in shape to :class:`Consolidator`:

    * **Consolidator** — receives raw working/episodic writes, returns derived
      semantic records to persist.
    * **Reconciler** — receives live, non-superseded semantic facts for a
      scope, returns soft-supersede decisions (no new rows are created).

    The ``MemoryStore.reconcile(memory_type, scope)`` method fetches the
    relevant non-superseded candidates, invokes the configured ``Reconciler``,
    and then applies each decision by calling
    :meth:`~agent_memory_sdk.repositories.facts.SemanticFactRepository.supersede`
    on the losing row.  The winning row is left untouched.

    Governance note
    ---------------
    Soft-supersession is **not** the same as :meth:`~agent_memory_sdk.store.MemoryStore.forget`:

    * ``deleted_at IS NOT NULL`` → "the user / operator asked us to forget
      this."  Set by ``forget()`` / ``soft_delete()``.
    * ``superseded_at IS NOT NULL`` → "we learned this was contradicted by a
      newer, more accurate fact."  Set by ``reconcile()``.

    Both mechanisms cause rows to be excluded from normal reads.  Keeping them
    as separate columns lets audit tooling distinguish explicit user erasure
    from AI-managed lifecycle events — a real governance distinction, not just
    a naming preference.

    **Sync path (default)**
    -----------------------
    Pass a ``Reconciler`` to :class:`~agent_memory_sdk.store.MemoryStore` at
    construction time::

        store = MemoryStore(pool, reconciler=MyLLMReconciler())

    Then call ``store.reconcile("facts", scope)`` explicitly — reconciliation
    is never triggered automatically.

    **LLM-based reconciler example**
    ---------------------------------
    ::

        import openai
        from agent_memory_sdk.types import Reconciler, SupersedeDecision

        class LLMReconciler:
            \"\"\"Detect contradictions in a list of semantic facts.\"\"\"

            def __init__(self, client: openai.OpenAI) -> None:
                self._client = client

            def __call__(
                self, candidates: list
            ) -> list[SupersedeDecision]:
                if len(candidates) < 2:
                    return []

                # Build an enumerated list for the LLM to reference by index.
                enumerated = "\\n".join(
                    f"{i}: {f.content}" for i, f in enumerate(candidates)
                )
                resp = self._client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are given numbered memory facts. "
                                "Find pairs where one fact directly contradicts "
                                "or supersedes another. For each such pair, "
                                "output a JSON line: "
                                '{{"winner": <index>, "loser": <index>, '
                                '"reason": "<short reason>"}}. '
                                "Output nothing if no contradictions exist."
                            ),
                        },
                        {"role": "user", "content": enumerated},
                    ],
                )
                text = resp.choices[0].message.content or ""
                decisions: list[SupersedeDecision] = []
                import json
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        w = candidates[int(obj["winner"])]
                        l = candidates[int(obj["loser"])]
                        decisions.append(
                            SupersedeDecision(
                                winner_id=w.id,
                                loser_id=l.id,
                                reason=obj.get("reason", "contradicts"),
                            )
                        )
                    except Exception:
                        continue
                return decisions

        # Wire in at store construction:
        store = MemoryStore(pool, reconciler=LLMReconciler(openai.OpenAI()))
        # Later, run a reconciliation pass:
        decisions = store.reconcile("facts", scope)
    """

    def __call__(self, candidates: list[SemanticFact]) -> list[SupersedeDecision]:
        """Identify facts that should be soft-superseded.

        Args:
            candidates: Live, non-superseded, non-deleted
                :class:`~agent_memory_sdk.models.SemanticFact` records for a
                single scope.  The Reconciler should examine these for
                contradictions and return decisions for any pairs where one
                fact supersedes another.

        Returns:
            A (possibly empty) list of :class:`SupersedeDecision` objects.
            Return ``[]`` if no contradictions are detected.
        """
        ...


class NoOpReconciler:
    """Default reconciler that does nothing.

    Always returns an empty decision list.  This is the default used by
    :class:`~agent_memory_sdk.store.MemoryStore` when no ``reconciler``
    argument is supplied — callers opt in to reconciliation explicitly.

    Because it returns an empty list, ``store.reconcile(...)`` with this
    default is a no-op: no facts are ever superseded automatically.
    """

    def __call__(self, candidates: list[SemanticFact]) -> list[SupersedeDecision]:
        return []


# ---------------------------------------------------------------------------
# IngestAction, IngestDecision, IngestResolver, NoOpIngestResolver
# ---------------------------------------------------------------------------

class IngestAction(str, enum.Enum):
    """The four actions an :class:`IngestResolver` can choose for an incoming write.

    ADD    — insert the candidate as a brand-new row (today's unchanged behavior).
    UPDATE — merge the candidate into an existing record identified by
             ``IngestDecision.target_id`` via the optimistic-concurrency
             ``update()`` path; no new row is inserted.
    DELETE — the candidate contradicts/invalidates an existing record; that
             record (``IngestDecision.target_id``) is tombstoned via
             ``forget()``; the candidate itself is not written.
    NOOP   — the candidate is redundant with an existing record; the write is
             skipped entirely, nothing is persisted.
    """

    ADD = "ADD"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    NOOP = "NOOP"


@dataclass
class IngestDecision:
    """A single ingest-time classification produced by an :class:`IngestResolver`.

    Attributes:
        action:     One of :class:`IngestAction` — ``ADD``, ``UPDATE``,
                    ``DELETE``, or ``NOOP``.
        target_id:  ``id`` of the existing record this decision applies to.
                    Required (non-``None``) for ``UPDATE`` and ``DELETE`` —
                    :meth:`~agent_memory_sdk.store.MemoryStore.remember` logs
                    a warning and falls back to ``ADD`` if it is missing.
                    Ignored for ``ADD`` and ``NOOP``.
        reason:     Human-readable explanation, e.g.
                    ``"duplicate of existing preference fact"`` or
                    ``"contradicts existing record: user moved cities"``.
                    Defaults to ``""``.
    """

    action: IngestAction
    target_id: str | None = None
    reason: str = ""


class IngestResolver(Protocol):
    """Protocol for pluggable ingest-time ADD/UPDATE/DELETE/NOOP classification.

    An ``IngestResolver`` is called synchronously by
    :meth:`~agent_memory_sdk.store.MemoryStore.remember`, **before** anything
    is written.  It receives the candidate record about to be remembered and
    the top-``resolver_k`` most-similar *existing* records already stored in
    the same scoped table (ranked by ascending cosine distance), and decides
    what should actually happen to the incoming write.

    Shape::

        (candidate: _MemoryBase, similar: list[tuple[_MemoryBase, float]]) -> IngestDecision

    This protocol is parallel in shape to :class:`Consolidator` and
    :class:`Reconciler`:

    * **Consolidator** — receives raw working/episodic writes *after* they
      land, returns derived semantic records to persist.
    * **Reconciler** — runs later, in batches, over already-written
      non-superseded facts, looking for contradictions *between* them.
    * **IngestResolver** — runs once, at write time, *before* the candidate
      is persisted, against the top-k most-similar existing records by
      cosine distance (not a batch scan), and can choose to merge / update /
      discard / no-op the incoming write itself.  This is the real-time
      classify-against-existing-similar-memories step Mem0's ingestion
      pipeline is built around.

    **How `MemoryStore.remember()` uses this:**

    1. Embed the candidate's content (using the record's own ``embedding``
       if already set, or the configured ``embedding_provider`` otherwise).
    2. Run ``search()`` against the same-type repository, scoped to the
       candidate's scope, with ``top_k=resolver_k``, to find the most
       similar existing records.
    3. Pair each result with its cosine distance to the candidate, in
       ascending-distance (most-similar-first) order, and call the
       ``IngestResolver`` with ``(candidate, similar)``.
    4. Act on the returned :class:`IngestDecision`:

       - ``ADD``    — ``repo.create(candidate, scope)`` (today's behavior).
       - ``UPDATE`` — fetch ``target_id`` via ``get_by_id()``, copy the
         candidate's ``content``/``metadata``/``embedding``/``confidence``
         onto it, and call ``repo.update()`` (optimistic concurrency).
       - ``DELETE`` — ``repo.forget(target_id, scope)``; the candidate is
         not written.
       - ``NOOP``   — nothing is written.

    **Sync path (default)**
    -----------------------
    Pass an ``IngestResolver`` to :class:`~agent_memory_sdk.store.MemoryStore`
    at construction time::

        store = MemoryStore(pool, ingest_resolver=MyLLMIngestResolver(), resolver_k=5)

    The resolver runs **synchronously** on the ``remember()`` call path,
    blocking until it returns — the same trade-off documented for
    ``Consolidator``.  The default :class:`NoOpIngestResolver` always returns
    ``ADD``, so ``remember()`` is byte-for-byte identical to pre-PIPE-2
    behavior (including skipping the extra ``search()`` round-trip entirely)
    when no resolver is configured.

    **LLM-based resolver example**
    -------------------------------
    ::

        import openai
        from agent_memory_sdk.types import IngestAction, IngestDecision, IngestResolver

        class LLMIngestResolver:
            \"\"\"Classify an incoming fact against similar existing facts.\"\"\"

            def __init__(self, client: openai.OpenAI) -> None:
                self._client = client

            def __call__(self, candidate, similar) -> IngestDecision:
                if not similar:
                    return IngestDecision(action=IngestAction.ADD)

                enumerated = "\\n".join(
                    f"{i}: {rec.content} (distance={dist:.3f})"
                    for i, (rec, dist) in enumerate(similar)
                )
                resp = self._client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "A new memory is about to be written. Given the "
                                "new memory and a list of similar existing "
                                "memories, decide ADD (distinct new fact), "
                                "UPDATE <index> (the new memory refines an "
                                "existing one), DELETE <index> (the new memory "
                                "contradicts and invalidates an existing one), "
                                "or NOOP (the new memory is redundant). "
                                "Respond with one word, optionally followed by "
                                "an index."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"New memory: {candidate.content}\\n\\n"
                            f"Similar existing memories:\\n{enumerated}",
                        },
                    ],
                )
                text = (resp.choices[0].message.content or "").strip()
                parts = text.split()
                verb = parts[0].upper() if parts else "ADD"
                if verb in ("UPDATE", "DELETE") and len(parts) > 1:
                    try:
                        idx = int(parts[1])
                        target_id = similar[idx][0].id
                        return IngestDecision(
                            action=IngestAction[verb],
                            target_id=target_id,
                            reason=f"LLM classified as {verb}",
                        )
                    except (ValueError, IndexError):
                        pass
                if verb == "NOOP":
                    return IngestDecision(action=IngestAction.NOOP)
                return IngestDecision(action=IngestAction.ADD)

        # Wire in at store construction:
        store = MemoryStore(pool, ingest_resolver=LLMIngestResolver(openai.OpenAI()))
    """

    def __call__(
        self,
        candidate: _MemoryBase,
        similar: list[tuple[_MemoryBase, float]],
    ) -> IngestDecision:
        """Classify an incoming write against similar existing records.

        Args:
            candidate: The record about to be written (not yet persisted;
                       ``id``/``created_at``/``version`` may be pre-populated
                       defaults, not server-assigned values).
            similar:   Up to ``resolver_k`` ``(existing_record, cosine_distance)``
                       tuples for the most similar existing records in the same
                       scoped table, in ascending-distance (most-similar-first)
                       order.  Empty when no existing records were found (or no
                       embedding could be computed for the candidate).

        Returns:
            An :class:`IngestDecision` naming the action to take.
        """
        ...


class NoOpIngestResolver:
    """Default ingest resolver: always ``ADD``.

    This is the default used by :class:`~agent_memory_sdk.store.MemoryStore`
    when no ``ingest_resolver`` argument is supplied — callers opt in to
    ingest-time classification explicitly.  Because this default always
    returns ``ADD``, and :meth:`~agent_memory_sdk.store.MemoryStore.remember`
    special-cases this exact class to skip the similarity ``search()`` call
    entirely, using the default reproduces today's unchanged ADD-only
    write behavior with zero added overhead.
    """

    def __call__(
        self,
        candidate: _MemoryBase,
        similar: list[tuple[_MemoryBase, float]],
    ) -> IngestDecision:
        return IngestDecision(action=IngestAction.ADD)


# ---------------------------------------------------------------------------
# ContextCard, Summarizer, NoOpSummarizer
# ---------------------------------------------------------------------------

@dataclass
class ContextCard:
    """A condensed view of recent working-memory turns for the active thread.

    Returned by :meth:`~agent_memory_sdk.store.MemoryStore.get_context_card`.

    Attributes:
        turns:              Recent working-memory records in **chronological
                            order** (oldest first), up to ``max_turns``.  Each
                            element is a fully-populated
                            :class:`~agent_memory_sdk.models.WorkingMemory`
                            instance.
        turn_count:         Total number of turns returned (``len(turns)``).
        latest_at:          Timestamp of the most-recently created turn, or
                            ``None`` if there are no turns at all.
        summary:            Optional narrative produced by a configured
                            :class:`Summarizer`.  ``None`` when no summarizer is
                            configured (the default).
        relevant_facts:     **PIPE-4.**  Durable :class:`~agent_memory_sdk.models.SemanticFact`
                            records retrieved by relevance to the ``query``
                            passed to :meth:`~agent_memory_sdk.store.MemoryStore.get_context_card`,
                            backfilled with the most-recent facts when the
                            relevance search returns fewer than the configured
                            minimum.  ``None`` unless the caller passed both
                            ``query`` and ``include_long_term=True`` — this
                            keeps the field absent (not merely empty) for the
                            default, ORC-1-compatible call shape.
        relevant_profiles:  **PIPE-4.**  Same as ``relevant_facts`` but for
                            :class:`~agent_memory_sdk.models.EntityProfile`
                            records.  ``None`` unless long-term blending was
                            requested.

    The ``summary`` field is intentionally separate from ``turns`` so callers
    who need structured access to individual messages can still use ``turns``
    even when a summarizer is configured.  ``relevant_facts``/``relevant_profiles``
    follow the same separation-of-concerns principle: the raw recent-turns
    view is never mutated by long-term blending, it is only ever supplemented.
    """

    turns: list[WorkingMemory] = field(default_factory=list)
    turn_count: int = 0
    latest_at: datetime | None = None
    summary: str | None = None
    relevant_facts: list[SemanticFact] | None = None
    relevant_profiles: list[EntityProfile] | None = None


class Summarizer(Protocol):
    """Protocol for pluggable context-card summarization callbacks.

    A ``Summarizer`` is called by
    :meth:`~agent_memory_sdk.store.MemoryStore.get_context_card` after
    the raw ``turns`` list is assembled.  It receives the turns in
    chronological order and returns a human-readable summary string.

    Shape::

        (turns: list[WorkingMemory]) -> str

    This protocol is parallel in shape to :class:`Consolidator` and
    :class:`Reconciler` — a single-callable protocol injected at
    :class:`~agent_memory_sdk.store.MemoryStore` construction time.

    **When to use a Summarizer:**

    * The default behavior (no summarizer) returns the raw ``turns`` list —
      no LLM call, no overhead.
    * Supply a :class:`Summarizer` when you want
      :meth:`~agent_memory_sdk.store.MemoryStore.get_context_card` to also
      produce a condensed narrative in addition to the raw turns.

    **LLM-based summarizer example**::

        import openai
        from agent_memory_sdk.models import WorkingMemory
        from agent_memory_sdk.types import Summarizer

        class LLMSummarizer:
            def __init__(self, client: openai.OpenAI) -> None:
                self._client = client

            def __call__(self, turns: list[WorkingMemory]) -> str:
                if not turns:
                    return ""
                combined = "\\n".join(t.content for t in turns)
                resp = self._client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Summarise the following conversation turns in "
                                "2-3 sentences, preserving key facts and context."
                            ),
                        },
                        {"role": "user", "content": combined},
                    ],
                )
                return resp.choices[0].message.content or ""

        store = MemoryStore(pool, summarizer=LLMSummarizer(openai.OpenAI()))
    """

    def __call__(self, turns: list[WorkingMemory]) -> str:
        """Summarise a list of working-memory turns into a narrative string.

        Args:
            turns: Working-memory records in chronological order (oldest
                   first).  May be empty — implementations should handle
                   the empty-list case gracefully.

        Returns:
            A human-readable summary string.  Return ``""`` for empty input.
        """
        ...


class NoOpSummarizer:
    """Default summarizer that does nothing.

    Returns ``""`` for any input.  This is the default used by
    :class:`~agent_memory_sdk.store.MemoryStore` when no ``summarizer``
    argument is supplied — callers opt in to summarization explicitly.
    """

    def __call__(self, turns: list[WorkingMemory]) -> str:
        return ""


# ---------------------------------------------------------------------------
# DistanceMetric
# ---------------------------------------------------------------------------

class DistanceMetric(str, enum.Enum):
    """Vector distance metrics supported by Db2 ``VECTOR_DISTANCE``.

    The value of each member is the string that Db2 accepts as the
    third argument to ``VECTOR_DISTANCE(col, ?, '<metric>')``.

    Note: the distance metric used in a search query MUST match the
    ``WITH DISTANCE <metric>`` clause of the table's VECTOR INDEX.
    All five memory tables are indexed WITH DISTANCE COSINE (see
    0002_memory_tables.sql).  Passing a non-COSINE metric at query time
    will still return results, but the ANN index will NOT be used — Db2
    will fall back to a full scan.
    """

    COSINE = "COSINE"
    EUCLIDEAN = "EUCLIDEAN"
    DOT = "DOT"
    MANHATTAN = "MANHATTAN"


# ---------------------------------------------------------------------------
# SearchMode
# ---------------------------------------------------------------------------

class SearchMode(str, enum.Enum):
    """Controls whether Db2 uses the ANN vector index or an exact scan.

    APPROX  → ``FETCH FIRST n ROWS ONLY APPROX``
        Uses the DiskANN vector index.  Fast, sub-linear, approximate.
        Requires RUNSTATS to have been run on the table; requires the
        query metric to match the index's ``WITH DISTANCE`` clause.

    EXACT   → ``FETCH FIRST n ROWS ONLY``
        Full sequential scan; always returns the true top-k.  Slower on
        large tables but no RUNSTATS dependency and metric-agnostic.

    DEFAULT → standard ``FETCH FIRST n ROWS ONLY``
        Alias for EXACT (the optimizer chooses whether to use the index).
    """

    APPROX = "APPROX"
    EXACT = "EXACT"
    DEFAULT = "DEFAULT"

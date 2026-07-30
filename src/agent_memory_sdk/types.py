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
    from agent_memory_sdk.models import SemanticFact, WorkingMemory, _MemoryBase

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
# ContextCard, Summarizer, NoOpSummarizer
# ---------------------------------------------------------------------------

@dataclass
class ContextCard:
    """A condensed view of recent working-memory turns for the active thread.

    Returned by :meth:`~agent_memory_sdk.store.MemoryStore.get_context_card`.

    Attributes:
        turns:          Recent working-memory records in **chronological order**
                        (oldest first), up to ``max_turns``.  Each element is a
                        fully-populated :class:`~agent_memory_sdk.models.WorkingMemory`
                        instance.
        turn_count:     Total number of turns returned (``len(turns)``).
        latest_at:      Timestamp of the most-recently created turn, or ``None``
                        if there are no turns at all.
        summary:        Optional narrative produced by a configured
                        :class:`Summarizer`.  ``None`` when no summarizer is
                        configured (the default).

    The ``summary`` field is intentionally separate from ``turns`` so callers
    who need structured access to individual messages can still use ``turns``
    even when a summarizer is configured.
    """

    turns: list[WorkingMemory] = field(default_factory=list)
    turn_count: int = 0
    latest_at: datetime | None = None
    summary: str | None = None


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

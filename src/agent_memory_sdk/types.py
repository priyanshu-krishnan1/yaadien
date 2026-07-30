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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agent_memory_sdk.models import _MemoryBase

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

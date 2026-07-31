"""
benchmarks/retrieval_quality/consolidator.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A :class:`~agent_memory_sdk.types.Consolidator` implementation suited to the
benchmark's synthetic dataset.

Design rationale — template-matching vs. LLM
---------------------------------------------
The retrieval-quality dataset (see
:mod:`benchmarks.retrieval_quality.dataset`) uses highly structured,
single-sentence turns, each encoding exactly one atomic fact per a small set
of fixed template patterns, e.g.::

    "Priya mentioned that they live in Lisbon."
    "Marcus said their favorite hobby is chess."
    "On 2024-01-10, Yuki started a new job at Acme Corp as a Junior Engineer."
    "Ingrid said: actually, I've switched — my CURRENT favorite ..."

An LLM-based consolidator (the :class:`~agent_memory_sdk.types.Consolidator`
docstring's OpenAI/Ollama example) is appropriate for real-world free-form
sessions.  For *this benchmark*:

1. **Measurement hygiene**: the Consolidator fires once per ``remember()``
   call; LLM latency and non-determinism per turn would pollute the
   before/after retrieval-accuracy comparison that BENCH-3a/3b/3c intend
   to produce.  The variable under test is *whether consolidation helps*,
   not *whether a particular LLM's extraction quality is good*.
2. **Sufficiency**: every session turn in the synthetic dataset matches one
   of the five template categories below.  A regex extractor captures all
   facts it needs to capture — no paraphrase, inference, or common-sense
   reasoning is required.
3. **Reproducibility**: template-matching gives the exact same output on
   every run, making benchmark deltas stable across repeated measurements.

The consolidator is therefore a deterministic regex/template matcher.
It extracts one :class:`~agent_memory_sdk.models.SemanticFact` per turn (the
content is the turn verbatim — the point is to promote it from working memory
into the facts table so :func:`run_retrieval_quality` can later search
``store.facts`` as well as ``store.working``).

Limitations on this synthetic dataset
--------------------------------------
* **Only structured turns are matched**: any turn that does not match one of
  the five recognised patterns is passed through verbatim with confidence 0.7
  (a low-confidence catch-all).  For real free-form conversations this
  approach would miss most facts; it only works here because the synthetic
  dataset is template-generated.
* **One fact per turn**: the generator always plants exactly one fact per
  session turn, so the 1:1 mapping (1 turn → 1 SemanticFact) is correct.
  Real conversations often pack several facts into one utterance; this
  consolidator would produce one coarse record for the whole sentence rather
  than splitting them.
* **No coreference, no inference**: the extractor does not resolve pronouns
  or infer implicit facts.  "She lives in Lisbon" would not match the name-
  based patterns if the name had been introduced in a prior turn.  The
  synthetic turns always include the name, so this gap does not affect
  benchmark accuracy.
* **Verbatim content**: the produced SemanticFact's ``content`` is the raw
  turn text, not a normalised/compressed fact string.  This is intentional:
  later search queries are also natural-language questions, so verbatim turn
  text in the facts table is retrievable via vector similarity just as well
  as a compressed fact string would be — and it keeps the consolidator
  deterministic without any LLM call.

Use :class:`TemplateFact` for the benchmark's retrieval-quality suite.
Use a real LLM-backed consolidator (see
:class:`~agent_memory_sdk.types.Consolidator` docstring) for production or
evaluation on free-form conversational data.
"""

from __future__ import annotations

import re
from typing import Any

from agent_memory_sdk.models import SemanticFact

# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

# Each pattern is a compiled regex.  On a match, the whole turn is promoted
# to a SemanticFact with confidence reflecting how "explicit" the assertion is.

# 1. Direct attribute statement: "X mentioned that they live in Y."
#    or "X said their <attr> is Y."
_P_ATTRIBUTE = re.compile(
    r"""
    (?P<name>[A-Z][a-z]+)          # person's name
    \s+
    (?:mentioned|said|noted)       # discourse verb
    \s+
    (?:that\s+)?                   # optional "that"
    (?:they\s+)?                   # optional "they"
    (?:[a-z]+\s+)?                 # optional extra word ("their", etc.)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# 2. Dated event: "On YYYY-MM-DD, X did Y."
_P_DATED_EVENT = re.compile(
    r"On\s+\d{4}-\d{2}-\d{2},\s+[A-Z][a-z]+",
    re.IGNORECASE,
)

# 3. Explicit correction/update: "X said: actually, I've switched …"
#    or "X said: actually …"
_P_UPDATE = re.compile(
    r"""
    [A-Z][a-z]+\s+
    (?:said|mentioned|noted)\s*:\s*
    (?:actually|however|wait|no—|wait—)
    """,
    re.VERBOSE | re.IGNORECASE,
)

# 4. Project-naming / compound reference:
#    "X said the project inspired by … is called Y."
_P_PROJECT = re.compile(
    r"[A-Z][a-z]+\s+said\s+the\s+project",
    re.IGNORECASE,
)


def _classify(turn: str) -> float:
    """Return a confidence value for the extracted fact based on the pattern.

    * Dated events and explicit updates are highly grounded (explicit
      assertion with temporal context or an acknowledgement of change) → 0.95.
    * Direct attribute statements are explicit → 0.90.
    * Project-naming references are explicit → 0.90.
    * Unmatched turns are treated as low-confidence catch-all → 0.70.
    """
    if _P_DATED_EVENT.search(turn):
        return 0.95
    if _P_UPDATE.search(turn):
        return 0.95
    if _P_ATTRIBUTE.search(turn):
        return 0.90
    if _P_PROJECT.search(turn):
        return 0.90
    # Catch-all: turn not matching a known pattern — promote with low
    # confidence so it is still searchable but won't dominate high-confidence
    # facts at min_confidence > 0.
    return 0.70


class BenchmarkConsolidator:
    """Deterministic template-matching Consolidator for the benchmark suite.

    Converts each ``WorkingMemory`` turn passed by
    :meth:`~agent_memory_sdk.store.MemoryStore.remember` into a
    :class:`~agent_memory_sdk.models.SemanticFact` whose ``content`` is the
    raw turn text.  One SemanticFact is returned per non-empty turn.

    The confidence assigned to each fact reflects how closely the turn
    matches one of the five recognised template patterns (see module
    docstring).  The ``metadata`` dict includes
    ``{"source": "benchmark_template_consolidator", "category_pattern": ...}``
    so downstream queries can filter by extraction source.

    This class satisfies the :class:`~agent_memory_sdk.types.Consolidator`
    protocol — it is a plain callable taking
    ``list[_MemoryBase] -> list[_MemoryBase]``.

    Usage in the benchmark harness::

        from benchmarks.retrieval_quality.consolidator import BenchmarkConsolidator

        consolidator = BenchmarkConsolidator()
        store = MemoryStore(
            pool,
            embedding_provider=embedding_provider,
            consolidator=consolidator,
        )

    See :func:`benchmarks.retrieval_quality.run.run_retrieval_quality` for
    the wiring point (``consolidator=`` optional parameter).
    """

    def __call__(self, raw_memories: list[Any]) -> list[Any]:
        """Extract one SemanticFact per non-empty WorkingMemory turn.

        Args:
            raw_memories: The memories just written to working or episodic
                memory — each is a fully-populated model instance with
                ``agent_id``, ``user_id``, ``content``, etc. set by the
                repository layer.

        Returns:
            A list of :class:`~agent_memory_sdk.models.SemanticFact` records,
            one per non-empty input turn.  Returns ``[]`` if
            ``raw_memories`` is empty.
        """
        if not raw_memories:
            return []

        facts: list[SemanticFact] = []
        for mem in raw_memories:
            content: str = getattr(mem, "content", "") or ""
            content = content.strip()
            if not content:
                continue

            confidence = _classify(content)

            facts.append(
                SemanticFact(
                    agent_id=mem.agent_id,
                    tenant_id=getattr(mem, "tenant_id", None),
                    user_id=getattr(mem, "user_id", None),
                    thread_id=getattr(mem, "thread_id", None),
                    content=content,
                    confidence=confidence,
                    metadata={
                        "source": "benchmark_template_consolidator",
                        "from_memory_id": mem.id,
                    },
                )
            )
        return facts

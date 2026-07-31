"""
benchmarks/retrieval_quality/reconciler.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A :class:`~agent_memory_sdk.types.Reconciler` implementation suited to the
benchmark's synthetic dataset.

Design rationale — template-matching vs. LLM
---------------------------------------------
The same reasoning that led BENCH-3a to choose a deterministic regex
Consolidator over an LLM-based one applies here (see
:mod:`benchmarks.retrieval_quality.consolidator` module docstring and the
DECISIONS.md BENCH-3b entry):

1. **Measurement hygiene**: the variable under test is *whether reconciliation
   (supersession) helps retrieval accuracy on* ``knowledge_update`` *questions*,
   not whether Ollama's contradiction-detection quality is high.  A
   non-deterministic LLM reconciler would introduce variance that obscures the
   before/after delta.
2. **Sufficiency**: every ``knowledge_update`` session in the synthetic dataset
   has exactly one explicit contradiction phrased as::

       "X said: actually, I've switched — my favorite ... is now Y, not Z anymore."

   The ``_P_UPDATE`` pattern in :mod:`benchmarks.retrieval_quality.consolidator`
   already identifies this template at confidence 0.95.  A reconciler only needs
   to detect whether two facts from the *same scope* share a common attribute
   value (one asserting the old value, one asserting the new value via the
   explicit correction phrase) and supersede the older one.
3. **Reproducibility**: deterministic output → the same seed produces the same
   supersession decisions on every run.

The reconciler is therefore a deterministic pattern-matcher that:

* Detects a ``knowledge_update`` contradiction by recognising the correction
  phrase ``"actually, I've switched"`` in a fact's content.
* For every "correction" fact, finds the most recent *earlier* fact in the same
  scope that mentions the same person and the *old* attribute value (the phrase
  ``"not X anymore"`` in the correction turn names it explicitly).
* Emits a :class:`~agent_memory_sdk.types.SupersedeDecision` with the correction
  fact as *winner* and the old-value fact as *loser*.

Limitations on this synthetic dataset
---------------------------------------
* **One contradiction type detected**: only the ``"actually, I've switched"``
  correction phrase is matched.  Real-world contradictions (paraphrase, implicit
  retraction, gradual position change) are not handled.
* **One attribute per scope**: the synthetic ``knowledge_update`` generator
  plants exactly one contradicted attribute per scope, so the reconciler's
  assumption of at most one loser per correction fact is always correct here.
  Real conversations can have multiple simultaneous contradictions; this
  reconciler processes them independently and is correct only if the same
  attribute is not contradicted more than once per scope.
* **Name-and-attribute string match**: the loser-identification logic relies on
  the person's name and old attribute value appearing in both the original fact
  and the correction turn.  It does not resolve pronouns or implicit references.
  This is sufficient because the synthetic generator always includes the full
  name and both values in the correction turn.
* **Session order proxy via list order**: :meth:`MemoryStore.reconcile` passes
  the result of ``facts.list_all()``, which returns records in
  reverse-chronological order (newest first).  The reconciler therefore treats
  the first item matching the correction pattern as the *winner* and any earlier
  (index-higher) item asserting the same person+old-value as the *loser*.

Use :class:`BenchmarkReconciler` for the benchmark's retrieval-quality suite.
Use a real LLM-backed reconciler (see
:class:`~agent_memory_sdk.types.Reconciler` docstring) for production or
evaluation on free-form conversational data.
"""

from __future__ import annotations

import re
from typing import Any

from agent_memory_sdk.types import SupersedeDecision

# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

# Matches the explicit correction/update turn template:
#   "X said: actually, I've switched — my favorite ... is now Y, not Z anymore."
# Capture groups:
#   name      — the person's name
#   new_val   — the new attribute value (word(s) before ", not")
#   old_val   — the old attribute value (word(s) after "not " and before " anymore")
_P_CORRECTION = re.compile(
    r"""
    (?P<name>[A-Z][a-z]+)              # person's name
    \s+
    (?:said|mentioned|noted)\s*:\s*    # discourse verb + colon
    (?:actually|however|wait)          # correction marker
    .*?                                # anything between (lazy)
    \bis\s+now\s+                      # "is now"
    (?P<new_val>[A-Za-z0-9_.+#\-]+     # new value (e.g. "Rust", "Go", "TypeScript")
        (?:\s+[A-Za-z0-9_.+#\-]+)?)   # allow one extra word (e.g. "light mode")
    ,?\s+not\s+                        # ", not "
    (?P<old_val>[A-Za-z0-9_.+#\-]+     # old value
        (?:\s+[A-Za-z0-9_.+#\-]+)?)   # allow one extra word
    \s+anymore
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Matches the original plain-attribute turn template (the fact to supersede):
#   "X mentioned that they ... is Y."  /  "X said their ... is Y."
# Used to verify a candidate loser mentions both the person and the old value.
_P_ATTRIBUTE = re.compile(
    r"(?P<name>[A-Z][a-z]+)\s+(?:mentioned|said|noted)",
    re.IGNORECASE,
)


def _parse_correction(content: str) -> tuple[str, str, str] | None:
    """Return (name, new_val, old_val) if *content* matches the correction template."""
    m = _P_CORRECTION.search(content)
    if not m:
        return None
    return m.group("name"), m.group("new_val").strip(), m.group("old_val").strip()


def _is_matching_loser(content: str, name: str, old_val: str) -> bool:
    """Return True if *content* appears to assert the old (stale) value for *name*.

    A fact is a candidate loser when:
    - it mentions the person's *name* (case-insensitive), AND
    - it mentions the *old_val* string (case-insensitive), AND
    - it does NOT itself contain the correction phrase (i.e. is not the winner
      that we already identified).
    """
    lower = content.lower()
    # Exclude the correction turn itself from being its own loser.
    if "actually" in lower and "switched" in lower:
        return False
    return name.lower() in lower and old_val.lower() in lower


class BenchmarkReconciler:
    """Deterministic template-matching Reconciler for the benchmark suite.

    Examines the list of live :class:`~agent_memory_sdk.models.SemanticFact`
    records produced by :class:`~benchmarks.retrieval_quality.consolidator.BenchmarkConsolidator`
    for a single scope, identifies any that follow the explicit correction
    template (``"actually, I've switched — my favorite ... is now Y, not X
    anymore"``), and emits a :class:`~agent_memory_sdk.types.SupersedeDecision`
    nominating the *earlier* plain-attribute fact (the stale one) as the loser.

    This class satisfies the :class:`~agent_memory_sdk.types.Reconciler`
    protocol — it is a plain callable taking
    ``list[SemanticFact] -> list[SupersedeDecision]``.

    Usage in the benchmark harness::

        from benchmarks.retrieval_quality.reconciler import BenchmarkReconciler
        from benchmarks.retrieval_quality.consolidator import BenchmarkConsolidator

        reconciler = BenchmarkReconciler()
        consolidator = BenchmarkConsolidator()
        store = MemoryStore(
            pool,
            embedding_provider=embedding_provider,
            consolidator=consolidator,
            reconciler=reconciler,
            enable_chunking=False,
        )
        # After writing all sessions for a question:
        store.reconcile("facts", scope)

    See :func:`benchmarks.retrieval_quality.run.run_retrieval_quality` for
    the wiring point (``reconciler=`` optional parameter).
    """

    def __call__(self, candidates: list[Any]) -> list[SupersedeDecision]:
        """Identify stale facts that should be soft-superseded.

        The ``candidates`` list is the result of
        ``store.facts.list_all(scope)``, which returns records in
        reverse-chronological order (newest first).  The reconciler therefore
        processes candidates top-to-bottom: a correction fact (winner) appears
        before the original attribute fact (loser) in this ordering.

        Args:
            candidates: Live, non-superseded, non-deleted
                :class:`~agent_memory_sdk.models.SemanticFact` records for a
                single scope, in reverse-chronological order.

        Returns:
            A (possibly empty) list of :class:`~agent_memory_sdk.types.SupersedeDecision`
            objects.  Returns ``[]`` if no corrections are found or if the
            candidate list has fewer than two entries.
        """
        if len(candidates) < 2:
            return []

        decisions: list[SupersedeDecision] = []

        for i, winner_fact in enumerate(candidates):
            content: str = getattr(winner_fact, "content", "") or ""
            parsed = _parse_correction(content)
            if parsed is None:
                continue  # not a correction turn

            name, new_val, old_val = parsed

            # Look at earlier facts (higher index = older in reverse order)
            # for a matching loser that asserts the old value.
            for loser_fact in candidates[i + 1 :]:
                loser_content: str = getattr(loser_fact, "content", "") or ""
                if _is_matching_loser(loser_content, name, old_val):
                    decisions.append(
                        SupersedeDecision(
                            winner_id=winner_fact.id,
                            loser_id=loser_fact.id,
                            reason=(
                                f"contradicts: {name}'s current value is "
                                f"{new_val!r}, not {old_val!r}"
                            ),
                        )
                    )
                    # One loser per correction is correct for this synthetic
                    # dataset (each scope has at most one contradicted attribute).
                    break

        return decisions

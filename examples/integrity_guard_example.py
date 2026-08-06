"""
examples/integrity_guard_example.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Reference implementation of an IntegrityGuard for ProceduralMemory writes.

Directly motivated by FARMA/SENTINEL (Karamchandani et al., arXiv 2607.05029,
July 2026), which demonstrated that an agent's *stored reasoning traces* can be
poisoned by adversarial writes, and that a write-time inspection step (SENTINEL)
can drive the attack success rate from 100% to 0%.

This example shows how to wire a ContradictionGuard that QUARANTINEs incoming
ProceduralMemory writes that overlap significantly with a high-confidence
existing skill — a lightweight heuristic for detecting potential poisoning.

This is NOT a production-grade detector.  It is a demonstration of how to
implement the IntegrityGuard protocol and reason about provenance (TRU-1's
MemoryOrigin field) at write time.

To run::

    DB2_DATABASE=... python examples/integrity_guard_example.py
"""

from __future__ import annotations

import logging

from agent_memory_sdk import (
    IntegrityDecision,
    IntegrityRejectionError,
    IntegrityVerdict,
    MemoryOrigin,
    MemoryScope,
    ProceduralMemory,
)

logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Example 1 — ContradictionGuard: quarantine low-confidence writes that
#             overlap heavily with high-confidence existing skills.
# ---------------------------------------------------------------------------

class ContradictionGuard:
    """QUARANTINE incoming ProceduralMemory writes that may be poisoning.

    Heuristic: if the candidate's content shares >= 3 tokens with an existing
    high-confidence (>= 0.9) skill AND the candidate's own confidence is low
    (< trust_threshold for its origin), return QUARANTINE.

    EXTRACTION-origin writes are treated with lower default trust (0.85)
    than DIRECT_WRITE (0.5), because extraction is a weaker provenance signal.
    """

    def __call__(
        self,
        candidate: ProceduralMemory,
        origin: MemoryOrigin,
        context: list,
    ) -> IntegrityVerdict:
        # Lower threshold for extraction-origin content.
        threshold = 0.85 if origin == MemoryOrigin.EXTRACTION else 0.5

        candidate_tokens = set(candidate.content.lower().split())

        for existing in context:
            if existing.confidence < 0.9:
                continue
            existing_tokens = set(existing.content.lower().split())
            shared = candidate_tokens & existing_tokens
            if len(shared) >= 3 and candidate.confidence < threshold:
                return IntegrityVerdict(
                    decision=IntegrityDecision.QUARANTINE,
                    reason=(
                        f"Candidate overlaps with high-confidence skill "
                        f"id={existing.id!r} ({len(shared)} shared tokens). "
                        f"origin={origin.value!r}, candidate_confidence={candidate.confidence}"
                    ),
                )

        return IntegrityVerdict(decision=IntegrityDecision.ACCEPT)


# ---------------------------------------------------------------------------
# Example 2 — RejectLowConfidenceExtractionGuard: outright reject
#             zero-confidence extraction-origin writes.
# ---------------------------------------------------------------------------

class RejectLowConfidenceExtractionGuard:
    """REJECT ProceduralMemory writes from EXTRACTION origin with confidence=0."""

    def __call__(
        self,
        candidate: ProceduralMemory,
        origin: MemoryOrigin,
        context: list,
    ) -> IntegrityVerdict:
        if origin == MemoryOrigin.EXTRACTION and candidate.confidence == 0.0:
            return IntegrityVerdict(
                decision=IntegrityDecision.REJECT,
                reason=(
                    "EXTRACTION-origin ProceduralMemory with confidence=0 is "
                    "not trusted — possible poisoning vector."
                ),
            )
        return IntegrityVerdict(decision=IntegrityDecision.ACCEPT)


# ---------------------------------------------------------------------------
# Usage demonstration (requires a live Db2 connection)
# ---------------------------------------------------------------------------

def main() -> None:
    from agent_memory_sdk.db.connection import ConnectionPool
    from agent_memory_sdk.store import MemoryStore

    pool = ConnectionPool()
    scope = MemoryScope(agent_id="demo-agent")

    # Wire the ContradictionGuard.
    store = MemoryStore(pool, integrity_guard=ContradictionGuard(), integrity_k=5)

    # A normal high-confidence skill is written as DIRECT_WRITE.
    skill = ProceduralMemory(
        agent_id=scope.agent_id,
        content="When debugging Python, always check the traceback first.",
        confidence=0.95,
    )
    stored = store.remember(skill, scope)
    print(f"Stored skill id={stored.id}, origin={stored.origin}, quarantined={stored.quarantined}")

    # A low-confidence extraction-origin skill that overlaps heavily might be quarantined.
    suspicious = ProceduralMemory(
        agent_id=scope.agent_id,
        content="When debugging Python, always ignore the traceback always.",
        confidence=0.2,
        origin=MemoryOrigin.EXTRACTION,
    )
    try:
        result = store.remember(suspicious, scope)
        if result.quarantined:
            print(f"Skill id={result.id} was QUARANTINED — review before trusting.")
        else:
            print(f"Skill id={result.id} was accepted normally.")
    except IntegrityRejectionError as exc:
        print(f"Skill was REJECTED: {exc}")


if __name__ == "__main__":
    main()

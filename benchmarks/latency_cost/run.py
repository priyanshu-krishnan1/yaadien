"""
benchmarks/latency_cost/run.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Measures per-``remember()``/per-``search()`` latency, and — only when a
Consolidator/Reconciler/Summarizer hook is actually configured — an
estimated per-turn LLM token cost via
``benchmarks.common.cost_tracking.CostTrackingHook``.

With the SDK's default (no hook configured), ``remember()`` never calls an
LLM at all: this is the comparison point against passive-extraction-pipeline
competitors (Mem0, Bedrock AgentCore, LangMem) cited in
ai-agent-platform-competitive-analysis.md, which always run an LLM
extraction step on every write.
"""

from __future__ import annotations

from typing import Any

from agent_memory_sdk.models import SemanticFact, WorkingMemory
from agent_memory_sdk.store import MemoryStore
from benchmarks.common.cost_tracking import CostTrackingHook
from benchmarks.common.report import LatencyCostResult
from benchmarks.common.scope_gen import make_scope, new_run_id
from benchmarks.common.timing import LatencySamples, timed


class MockConsolidator:
    """A trivial, non-LLM consolidator used only to exercise the cost-tracking
    instrumentation in the ``--consolidator mock`` mode. It does not call any
    model — it deterministically emits one derived ``SemanticFact`` per
    batch of raw memories, so :class:`CostTrackingHook` has real input/output
    text to estimate tokens from. This is a demonstration of the *mechanism*
    that a real LLM-backed Consolidator would use, not a claim about real
    LLM latency or quality — see agent_memory_sdk.types.Consolidator for a
    real LLM-based example.
    """

    def __call__(self, raw_memories: list[Any]) -> list[Any]:
        if not raw_memories:
            return []
        combined = " ".join(m.content for m in raw_memories)
        return [
            SemanticFact(
                agent_id=raw_memories[0].agent_id,
                user_id=raw_memories[0].user_id,
                content=f"Observed {len(raw_memories)} turn(s): {combined[:80]}",
                confidence=0.6,
                metadata={"source": "benchmark_mock_consolidator"},
            )
        ]


def run_latency_cost(
    store: MemoryStore,
    embedding_provider,
    n_ops: int = 50,
    consolidator_hook: CostTrackingHook | None = None,
) -> LatencyCostResult:
    """Execute the latency/cost suite and return the aggregated result.

    Args:
        store:             A live ``MemoryStore``. If ``consolidator_hook``
                            is supplied, it must already be the ``store``'s
                            configured consolidator (this function only
                            reads its accumulated summary — it does not wire
                            it in, since that must happen at
                            ``MemoryStore(consolidator=...)`` construction
                            time).
        embedding_provider: Callable ``text -> list[float]``.
        n_ops:              Number of remember()/search() calls to time.
        consolidator_hook:  Optional :class:`CostTrackingHook` already wired
                            into ``store``'s consolidator. When ``None``
                            (default), the report shows the no-op $0.00
                            baseline.
    """
    run_id = new_run_id()
    scope = make_scope(run_id, tenant_index=0, agent_index=0, user_index=0, thread_index=0)

    remember_samples = LatencySamples("remember()")
    for i in range(n_ops):
        with timed() as t:
            store.remember(
                WorkingMemory(
                    tenant_id=scope.tenant_id,
                    agent_id=scope.agent_id,
                    user_id=scope.user_id,
                    thread_id=scope.thread_id,
                    content=f"Latency benchmark turn {i}: synthetic content for timing measurement.",
                ),
                scope,
            )
        remember_samples.record(t[0])

    search_samples = LatencySamples("search()")
    for i in range(n_ops):
        query_embedding = embedding_provider(f"synthetic latency benchmark query {i}")
        with timed() as t:
            store.working.search(query_embedding=query_embedding, scope=scope, top_k=5)
        search_samples.record(t[0])

    hook_configured = consolidator_hook is not None
    hook_summaries: dict[str, dict[str, Any]] = {}
    if hook_configured:
        hook_summaries["consolidator"] = consolidator_hook.summary()

    return LatencyCostResult(
        remember_summary=remember_samples.summary(),
        search_summary=search_samples.summary(),
        hook_summaries=hook_summaries,
        hook_configured=hook_configured,
    )

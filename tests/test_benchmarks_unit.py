"""
tests/test_benchmarks_unit.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Fast, dependency-free unit tests for the pure-logic pieces of the
``benchmarks/`` harness: scope generation, the dependency-free embedder,
and cost-tracking arithmetic.

The tests for the discarded harness components (timing.py, report.py,
llm_judge.py, retrieval_quality/dataset.py) were removed by BM-2 (EPIC-13)
as part of retiring the bespoke timing/report/judge/dataset modules.  Only
tests covering modules that survived the Phase 1b audit (KEEP verdict) are
retained here.
"""

from __future__ import annotations

from benchmarks.common.cost_tracking import CostTrackingHook
from benchmarks.common.embedding_providers import HashingEmbeddingProvider
from benchmarks.common.scope_gen import make_scope, marker_for, new_run_id

# ---------------------------------------------------------------------------
# scope_gen
# ---------------------------------------------------------------------------

def test_make_scope_builds_expected_hierarchy():
    scope = make_scope("run1", tenant_index=0, agent_index=1, user_index=2, thread_index=3)
    assert scope.tenant_id == "bench-run1-tenant-0"
    assert scope.agent_id == "bench-run1-tenant-0-agent-1"
    assert scope.user_id == "bench-run1-user-2"
    assert scope.thread_id == "bench-run1-thread-3"


def test_make_scope_omits_optional_fields_when_not_given():
    scope = make_scope("run1", tenant_index=0, agent_index=1)
    assert scope.user_id is None
    assert scope.thread_id is None


def test_marker_for_is_unique_per_scope():
    a = make_scope("run1", tenant_index=0, agent_index=1)
    b = make_scope("run1", tenant_index=0, agent_index=2)
    assert marker_for(a) != marker_for(b)


def test_new_run_id_is_unique():
    assert new_run_id() != new_run_id()


# ---------------------------------------------------------------------------
# embedding_providers.HashingEmbeddingProvider
# ---------------------------------------------------------------------------

def test_hashing_embedder_is_deterministic():
    embed = HashingEmbeddingProvider(dim=64)
    assert embed("hello world") == embed("hello world")


def test_hashing_embedder_returns_correct_dimension():
    embed = HashingEmbeddingProvider(dim=64)
    assert len(embed("hello world")) == 64


def test_hashing_embedder_normalizes_to_unit_length():
    embed = HashingEmbeddingProvider(dim=64)
    vec = embed("hello world this has several distinct tokens")
    norm = sum(v * v for v in vec) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_hashing_embedder_handles_empty_text():
    embed = HashingEmbeddingProvider(dim=32)
    vec = embed("")
    assert vec == [0.0] * 32


# ---------------------------------------------------------------------------
# cost_tracking.CostTrackingHook
# ---------------------------------------------------------------------------

def test_cost_tracking_hook_counts_calls_and_estimates_tokens():
    def fake_consolidator(raw_memories):
        return []

    hook = CostTrackingHook(wrapped=fake_consolidator, cost_per_1k_tokens_usd=1.0)
    hook(["some raw memory content here"])
    hook(["more raw memory content"])

    assert hook.call_count == 2
    assert hook.total_estimated_tokens > 0
    assert hook.total_estimated_cost_usd > 0.0
    summary = hook.summary()
    assert summary["hook_call_count"] == 2


def test_cost_tracking_hook_zero_calls_zero_cost():
    hook = CostTrackingHook(wrapped=lambda *_a, **_k: [])
    assert hook.call_count == 0
    assert hook.total_estimated_cost_usd == 0.0

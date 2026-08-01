"""
tests/test_benchmarks_unit.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Fast, dependency-free unit tests for the pure-logic pieces of the
``benchmarks/`` harness: dataset generation, the fallback judge/embedder,
cost-tracking arithmetic, latency percentiles, and report rendering.

These do NOT require a live Db2 instance or any network access — they cover
the parts of the harness that can be verified locally without the full
end-to-end run documented in benchmarks/README.md. Not gated behind the
``integration`` marker; they always run as part of the normal test suite.
"""

from __future__ import annotations

from benchmarks.common.cost_tracking import CostTrackingHook
from benchmarks.common.embedding_providers import HashingEmbeddingProvider
from benchmarks.common.llm_judge import KeywordMatchJudge
from benchmarks.common.report import (
    CategoryScore,
    IsolationLoadResult,
    LatencyCostResult,
    RetrievalQualityResult,
    RunMetadata,
    render_markdown,
)
from benchmarks.common.scope_gen import make_scope, marker_for, new_run_id
from benchmarks.common.timing import LatencySamples
from benchmarks.retrieval_quality.dataset import ABILITY_CATEGORIES, generate_dataset

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
# retrieval_quality.dataset
# ---------------------------------------------------------------------------

def test_generate_dataset_shape():
    run_id = new_run_id()
    dataset = generate_dataset(run_id, n_per_category=3, seed=1)
    assert len(dataset) == 5 * 3
    categories_seen = {q.category for q in dataset}
    assert categories_seen == set(ABILITY_CATEGORIES)


def test_generate_dataset_is_deterministic_given_seed():
    run_id = "fixed-run"
    a = generate_dataset(run_id, n_per_category=2, seed=7)
    b = generate_dataset(run_id, n_per_category=2, seed=7)
    assert [q.question for q in a] == [q.question for q in b]
    assert [q.gold_answer for q in a] == [q.gold_answer for q in b]


def test_abstention_category_has_empty_gold_answer():
    run_id = new_run_id()
    dataset = generate_dataset(run_id, n_per_category=2, seed=1)
    abstention_questions = [q for q in dataset if q.category == "abstention"]
    assert abstention_questions
    assert all(q.gold_answer == "" for q in abstention_questions)


def test_non_abstention_categories_have_nonempty_gold_answer():
    run_id = new_run_id()
    dataset = generate_dataset(run_id, n_per_category=2, seed=1)
    for q in dataset:
        if q.category != "abstention":
            assert q.gold_answer != ""


def test_every_question_has_a_unique_scope():
    run_id = new_run_id()
    dataset = generate_dataset(run_id, n_per_category=4, seed=1)
    agent_ids = [q.scope.agent_id for q in dataset]
    assert len(agent_ids) == len(set(agent_ids))


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
# llm_judge.KeywordMatchJudge
# ---------------------------------------------------------------------------

def test_keyword_judge_scores_true_on_strong_overlap():
    judge = KeywordMatchJudge()
    assert judge("What city?", "Lisbon", "The user mentioned they live in Lisbon.") is True


def test_keyword_judge_scores_false_on_no_overlap():
    judge = KeywordMatchJudge()
    assert judge("What city?", "Lisbon", "The weather today is sunny and warm.") is False


def test_keyword_judge_abstention_true_on_empty_context():
    judge = KeywordMatchJudge()
    assert judge("What is their shoe size?", "", "") is True


def test_keyword_judge_abstention_false_when_context_is_nonempty():
    judge = KeywordMatchJudge()
    assert judge("What is their shoe size?", "", "The user lives in Lisbon.") is False


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


# ---------------------------------------------------------------------------
# timing.LatencySamples
# ---------------------------------------------------------------------------

def test_latency_samples_percentiles_on_known_data():
    samples = LatencySamples(label="test")
    for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
        samples.record(float(v))
    assert samples.count == 10
    assert samples.percentile(50) in (50.0, 60.0)  # nearest-rank, no interpolation
    assert samples.percentile(100) == 100.0
    assert samples.percentile(0) == 10.0


def test_latency_samples_summary_empty():
    samples = LatencySamples(label="empty")
    summary = samples.summary()
    assert summary["count"] == 0


# ---------------------------------------------------------------------------
# report.render_markdown
# ---------------------------------------------------------------------------

def test_render_markdown_flags_fallback_components_as_not_comparable():
    metadata = RunMetadata(run_id="r1", embedding_provider="hashing", embedding_dim=64)
    retrieval = RetrievalQualityResult(
        judge_name="keyword",
        embedding_provider="hashing",
        top_k=5,
        category_scores=[CategoryScore(category="extraction", correct=3, total=4)],
        is_longmemeval_comparable=False,
        deviation_notes=["Embedding provider 'hashing' is a lexical-overlap fallback."],
    )
    md = render_markdown(metadata, retrieval=retrieval)
    assert "NOT a LongMemEval-comparable number" in md
    assert "75.0%" in md  # 3/4


def test_render_markdown_omits_deviation_warning_when_comparable():
    metadata = RunMetadata(run_id="r1", embedding_provider="sentence-transformers", embedding_dim=384)
    retrieval = RetrievalQualityResult(
        judge_name="gemini",
        embedding_provider="sentence-transformers",
        top_k=5,
        category_scores=[CategoryScore(category="extraction", correct=4, total=4)],
        is_longmemeval_comparable=True,
        deviation_notes=[],
    )
    md = render_markdown(metadata, retrieval=retrieval)
    assert "NOT a LongMemEval-comparable number" not in md
    assert "matches the LongMemEval" in md


def test_render_markdown_no_op_cost_baseline():
    metadata = RunMetadata(run_id="r1", embedding_provider="hashing", embedding_dim=64)
    latency = LatencyCostResult(
        remember_summary={"label": "remember()", "count": 0},
        search_summary={"label": "search()", "count": 0},
        hook_summaries={},
        hook_configured=False,
    )
    md = render_markdown(metadata, latency=latency)
    assert "$0.00" in md


def test_render_markdown_isolation_pass_and_fail_verdicts():
    metadata = RunMetadata(run_id="r1", embedding_provider="hashing", embedding_dim=64)

    passing = IsolationLoadResult(
        tenants=2, agents_per_tenant=1, concurrent_workers=2, ops_per_worker=1,
        total_write_ops=2, total_read_assertions=2, leakage_incidents=0, elapsed_s=0.1,
    )
    md_pass = render_markdown(metadata, isolation=passing)
    assert "PASS" in md_pass

    failing = IsolationLoadResult(
        tenants=2, agents_per_tenant=1, concurrent_workers=2, ops_per_worker=1,
        total_write_ops=2, total_read_assertions=2, leakage_incidents=1, elapsed_s=0.1,
    )
    md_fail = render_markdown(metadata, isolation=failing)
    assert "FAIL" in md_fail

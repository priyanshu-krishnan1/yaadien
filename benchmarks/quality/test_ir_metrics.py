"""
benchmarks/quality/test_ir_metrics.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-17 acceptance tests: Deterministic IR metrics (CI-safe).

These tests verify:
  1. Bit-identical results on repeated calls with the same inputs (determinism)
  2. Recall@k correctness: full recall, zero recall, partial recall
  3. MRR correctness at ranks 1, 2, 3 and when no hit exists
  4. nDCG@k correctness for a 3-result list with one relevant at rank 2
  5. Edge cases: empty retrieval → 0.0, all-relevant → 1.0
  6. CategoryIRMetrics averages correctly across multiple questions
  7. compute_ir_metrics handles all four k values in one call
  8. format_markdown_table produces a table with the expected headings

All tests are marked ``benchmark_micro`` — no Db2 required.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pytest

from benchmarks.quality.ir_metrics import (
    compute_ir_metrics,
    format_markdown_table,
    mrr,
    ndcg_at_k,
    recall_at_k,
)

# ---------------------------------------------------------------------------
# Minimal stub for LongMemEvalQuestion (avoids DB / HF imports in micro tests)
# ---------------------------------------------------------------------------


@dataclass
class _StubQuestion:
    """Minimal stand-in for LongMemEvalQuestion used in pure-Python tests."""

    question_id: str
    category: str
    evidence_session_ids: set[str]
    question: str = "stub question"
    gold_answer: str = "stub answer"
    haystack_messages: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ids(*names: str) -> list[str]:
    """Short helper — turn positional strings into a ranked list."""
    return list(names)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_recall_at_k_is_deterministic() -> None:
    """Calling recall_at_k twice with the same inputs returns bit-identical floats."""
    retrieved = _ids("s1", "s2", "s3", "s4", "s5")
    evidence = {"s1", "s3"}
    r1 = recall_at_k(retrieved, evidence, k=5)
    r2 = recall_at_k(retrieved, evidence, k=5)
    assert r1 == r2  # float bit equality, not approx


@pytest.mark.benchmark_micro
def test_mrr_is_deterministic() -> None:
    """Calling mrr twice with the same inputs returns bit-identical floats."""
    retrieved = _ids("s1", "s2", "s3")
    evidence = {"s2"}
    v1 = mrr(retrieved, evidence)
    v2 = mrr(retrieved, evidence)
    assert v1 == v2


@pytest.mark.benchmark_micro
def test_ndcg_at_k_is_deterministic() -> None:
    """Calling ndcg_at_k twice with the same inputs returns bit-identical floats."""
    retrieved = _ids("s1", "s2", "s3")
    evidence = {"s2"}
    v1 = ndcg_at_k(retrieved, evidence, k=3)
    v2 = ndcg_at_k(retrieved, evidence, k=3)
    assert v1 == v2


@pytest.mark.benchmark_micro
def test_compute_ir_metrics_is_deterministic() -> None:
    """compute_ir_metrics returns identical IRRunSummary on repeated calls."""
    questions = [
        _StubQuestion("q1", "multi-session", {"s1", "s2"}),
        _StubQuestion("q2", "abstention", set()),
    ]
    pairs = [
        (questions[0], _ids("s1", "s3", "s2")),
        (questions[1], _ids("s4", "s5")),
    ]
    s1 = compute_ir_metrics(pairs, k_values=(5, 10), run_id="fixed-run-id")
    s2 = compute_ir_metrics(pairs, k_values=(5, 10), run_id="fixed-run-id")
    assert s1.overall.recall == s2.overall.recall
    assert s1.overall.mrr == s2.overall.mrr
    assert s1.overall.ndcg == s2.overall.ndcg


# ---------------------------------------------------------------------------
# recall_at_k
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_recall_at_k_full_recall() -> None:
    """Recall is 1.0 when all evidence sessions appear in the top-k results."""
    retrieved = _ids("s1", "s2", "s3", "s4", "s5")
    evidence = {"s1", "s3"}
    assert recall_at_k(retrieved, evidence, k=5) == 1.0


@pytest.mark.benchmark_micro
def test_recall_at_k_zero_recall() -> None:
    """Recall is 0.0 when none of the evidence sessions appear in the top-k."""
    retrieved = _ids("s1", "s2", "s3")
    evidence = {"s4", "s5"}
    assert recall_at_k(retrieved, evidence, k=3) == 0.0


@pytest.mark.benchmark_micro
def test_recall_at_k_partial_recall() -> None:
    """Recall is fractional when only some evidence sessions are retrieved."""
    retrieved = _ids("s1", "s2", "s3")
    evidence = {"s1", "s4"}  # s1 hit, s4 miss → 0.5
    assert recall_at_k(retrieved, evidence, k=3) == 0.5


@pytest.mark.benchmark_micro
def test_recall_at_k_cutoff_respected() -> None:
    """Evidence sessions beyond rank k do not contribute to Recall@k."""
    retrieved = _ids("s1", "s2", "s3", "s4", "s5")
    evidence = {"s5"}
    assert recall_at_k(retrieved, evidence, k=4) == 0.0
    assert recall_at_k(retrieved, evidence, k=5) == 1.0


@pytest.mark.benchmark_micro
def test_recall_at_k_empty_retrieval_returns_zero() -> None:
    """Empty retrieval list → 0.0 regardless of evidence set."""
    assert recall_at_k([], {"s1", "s2"}, k=5) == 0.0


@pytest.mark.benchmark_micro
def test_recall_at_k_empty_evidence_returns_zero() -> None:
    """Empty evidence set → 0.0 (no relevant sessions to retrieve)."""
    assert recall_at_k(_ids("s1", "s2", "s3"), set(), k=3) == 0.0


@pytest.mark.benchmark_micro
def test_recall_at_k_all_relevant() -> None:
    """Recall is 1.0 when every retrieved session is relevant."""
    retrieved = _ids("s1", "s2", "s3")
    evidence = {"s1", "s2", "s3"}
    assert recall_at_k(retrieved, evidence, k=3) == 1.0


# ---------------------------------------------------------------------------
# MRR
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_mrr_hit_at_rank_1() -> None:
    """MRR is 1.0 when the first result is relevant."""
    assert mrr(_ids("s1", "s2", "s3"), {"s1"}) == 1.0


@pytest.mark.benchmark_micro
def test_mrr_hit_at_rank_2() -> None:
    """MRR is 0.5 when the first relevant result is at rank 2."""
    assert mrr(_ids("s1", "s2", "s3"), {"s2"}) == 0.5


@pytest.mark.benchmark_micro
def test_mrr_hit_at_rank_3() -> None:
    """MRR is 1/3 when the first relevant result is at rank 3."""
    result = mrr(_ids("s1", "s2", "s3"), {"s3"})
    assert math.isclose(result, 1.0 / 3.0)


@pytest.mark.benchmark_micro
def test_mrr_no_hit_returns_zero() -> None:
    """MRR is 0.0 when no relevant session appears in the list."""
    assert mrr(_ids("s1", "s2", "s3"), {"s4"}) == 0.0


@pytest.mark.benchmark_micro
def test_mrr_empty_retrieval_returns_zero() -> None:
    """MRR is 0.0 when the retrieval list is empty."""
    assert mrr([], {"s1"}) == 0.0


@pytest.mark.benchmark_micro
def test_mrr_empty_evidence_returns_zero() -> None:
    """MRR is 0.0 when the evidence set is empty."""
    assert mrr(_ids("s1", "s2"), set()) == 0.0


@pytest.mark.benchmark_micro
def test_mrr_uses_first_hit_only() -> None:
    """MRR scores only the rank of the FIRST relevant result, not subsequent ones."""
    # s1 is relevant at rank 1, s3 is also relevant at rank 3 — MRR uses rank 1.
    assert mrr(_ids("s1", "s2", "s3"), {"s1", "s3"}) == 1.0


# ---------------------------------------------------------------------------
# nDCG@k
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_ndcg_at_k_spot_check_rank_2() -> None:
    """nDCG when one relevant session is at rank 2 in a 3-result list."""
    # retrieved: [s1(irrelevant), s2(relevant), s3(irrelevant)]
    # DCG@3  = 1/log2(3) (hit at rank 2)
    # IDCG@3 = 1/log2(2) = 1.0 (perfect: relevant session at rank 1)
    retrieved = _ids("s1", "s2", "s3")
    evidence = {"s2"}
    expected = (1.0 / math.log2(3)) / (1.0 / math.log2(2))
    result = ndcg_at_k(retrieved, evidence, k=3)
    assert math.isclose(result, expected, rel_tol=1e-12)


@pytest.mark.benchmark_micro
def test_ndcg_at_k_perfect_ranking() -> None:
    """nDCG is 1.0 when all evidence sessions are at the top of the ranking."""
    retrieved = _ids("s1", "s2", "s3", "s4")
    evidence = {"s1", "s2"}
    assert math.isclose(ndcg_at_k(retrieved, evidence, k=4), 1.0)


@pytest.mark.benchmark_micro
def test_ndcg_at_k_no_hit_returns_zero() -> None:
    """nDCG is 0.0 when no relevant session appears in the top-k."""
    retrieved = _ids("s1", "s2", "s3")
    evidence = {"s4"}
    assert ndcg_at_k(retrieved, evidence, k=3) == 0.0


@pytest.mark.benchmark_micro
def test_ndcg_at_k_empty_retrieval_returns_zero() -> None:
    """nDCG is 0.0 when the retrieval list is empty."""
    assert ndcg_at_k([], {"s1"}, k=5) == 0.0


@pytest.mark.benchmark_micro
def test_ndcg_at_k_empty_evidence_returns_zero() -> None:
    """nDCG is 0.0 when the evidence set is empty."""
    assert ndcg_at_k(_ids("s1", "s2"), set(), k=2) == 0.0


@pytest.mark.benchmark_micro
def test_ndcg_at_k_cutoff_restricts_dcg() -> None:
    """A relevant session beyond the cutoff k is not counted in DCG@k."""
    retrieved = _ids("s1", "s2", "s3", "s4", "s5")
    evidence = {"s5"}
    assert ndcg_at_k(retrieved, evidence, k=4) == 0.0  # s5 outside cutoff

    # s5 is at rank 5 inside a k=5 window.
    # DCG@5  = 1/log2(6)
    # IDCG@5 = 1/log2(2) = 1.0  (perfect: the one relevant doc at rank 1)
    expected = (1.0 / math.log2(6)) / (1.0 / math.log2(2))
    assert math.isclose(ndcg_at_k(retrieved, evidence, k=5), expected, rel_tol=1e-12)


@pytest.mark.benchmark_micro
def test_ndcg_at_k_result_in_0_1_range() -> None:
    """nDCG@k is always in [0.0, 1.0]."""
    retrieved = _ids("s3", "s1", "s2")
    evidence = {"s1", "s2"}
    for k in (1, 2, 3, 5):
        v = ndcg_at_k(retrieved, evidence, k=k)
        assert 0.0 <= v <= 1.0, f"nDCG@{k} = {v} is out of range"


# ---------------------------------------------------------------------------
# CategoryIRMetrics aggregation
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_category_ir_metrics_averages_correctly() -> None:
    """compute_ir_metrics averages per-category scores correctly."""
    questions = [
        _StubQuestion("q1", "knowledge-update", {"s1"}),
        _StubQuestion("q2", "knowledge-update", {"s2"}),
    ]
    # q1: s1 at rank 1 → recall=1.0, mrr=1.0, ndcg=1.0 for any k≥1
    # q2: s2 NOT in retrieved → recall=0.0, mrr=0.0, ndcg=0.0
    pairs = [
        (questions[0], _ids("s1", "s3")),
        (questions[1], _ids("s3", "s4")),
    ]
    summary = compute_ir_metrics(pairs, k_values=(5,), run_id="test-run")
    cat = summary.per_category["knowledge-update"]

    assert cat.n_questions == 2
    assert math.isclose(cat.recall[5], 0.5)
    assert math.isclose(cat.mrr[5], 0.5)
    assert math.isclose(cat.ndcg[5], 0.5)


@pytest.mark.benchmark_micro
def test_category_ir_metrics_single_question() -> None:
    """A category with one question has averages equal to that question's scores."""
    q = _StubQuestion("q1", "abstention", set())
    pairs = [(q, _ids("s1", "s2"))]
    summary = compute_ir_metrics(pairs, k_values=(5,), run_id="test-run")
    cat = summary.per_category["abstention"]

    assert cat.n_questions == 1
    # Evidence set is empty → all metrics are 0.0
    assert cat.recall[5] == 0.0
    assert cat.mrr[5] == 0.0
    assert cat.ndcg[5] == 0.0


# ---------------------------------------------------------------------------
# compute_ir_metrics — all four k values
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_compute_ir_metrics_all_four_k_values() -> None:
    """compute_ir_metrics computes metrics for all of k in {5, 10, 20, 50}."""
    q = _StubQuestion("q1", "multi-session", {"s1", "s2"})
    pairs = [(q, _ids(*[f"s{i}" for i in range(1, 51)]))]
    summary = compute_ir_metrics(pairs, k_values=(5, 10, 20, 50), run_id="test-run")

    assert summary.k_values == (5, 10, 20, 50)
    for k in (5, 10, 20, 50):
        assert k in summary.overall.recall
        assert k in summary.overall.mrr
        assert k in summary.overall.ndcg


@pytest.mark.benchmark_micro
def test_compute_ir_metrics_recall_increases_with_k() -> None:
    """Recall@k is non-decreasing as k grows (more results can only help)."""
    # evidence: s1 at rank 1, s20 at rank 20, s50 at rank 50
    retrieved = [f"s{i}" for i in range(1, 51)]
    q = _StubQuestion("q1", "temporal-reasoning", {"s1", "s20", "s50"})
    pairs = [(q, retrieved)]
    summary = compute_ir_metrics(pairs, k_values=(5, 10, 20, 50), run_id="mono-test")
    recalls = [summary.overall.recall[k] for k in (5, 10, 20, 50)]
    assert recalls == sorted(recalls), f"Recall not monotone: {recalls}"


@pytest.mark.benchmark_micro
def test_compute_ir_metrics_overall_aggregates_all_categories() -> None:
    """Overall metrics macro-average over all questions across all categories."""
    questions = [
        _StubQuestion("q1", "abstention", set()),          # mrr=0.0 for any k
        _StubQuestion("q2", "knowledge-update", {"s1"}),   # mrr=1.0 at rank 1
    ]
    pairs = [
        (questions[0], _ids("s2")),
        (questions[1], _ids("s1")),
    ]
    summary = compute_ir_metrics(pairs, k_values=(5,), run_id="overall-test")
    # Overall MRR should be (0.0 + 1.0) / 2 = 0.5
    assert math.isclose(summary.overall.mrr[5], 0.5)


@pytest.mark.benchmark_micro
def test_compute_ir_metrics_empty_input() -> None:
    """compute_ir_metrics on an empty iterable returns a zero-question summary."""
    summary = compute_ir_metrics([], k_values=(5, 10), run_id="empty-run")
    assert summary.overall.n_questions == 0
    assert summary.per_category == {}
    for k in (5, 10):
        assert summary.overall.recall[k] == 0.0
        assert summary.overall.mrr[k] == 0.0
        assert summary.overall.ndcg[k] == 0.0


# ---------------------------------------------------------------------------
# format_markdown_table
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_format_markdown_table_contains_k_columns() -> None:
    """The rendered table has one column group per k value."""
    q = _StubQuestion("q1", "multi-session", {"s1"})
    pairs = [(q, _ids("s1"))]
    summary = compute_ir_metrics(pairs, k_values=(5, 10, 20, 50), run_id="fmt-test")
    table = format_markdown_table(summary)

    for k in (5, 10, 20, 50):
        assert f"R@{k}" in table, f"R@{k} column missing"
        assert f"nDCG@{k}" in table, f"nDCG@{k} column missing"
        assert f"MRR@{k}" in table, f"MRR@{k} column missing"


@pytest.mark.benchmark_micro
def test_format_markdown_table_contains_category_row() -> None:
    """Each category seen in the data produces a row in the Markdown table."""
    questions = [
        _StubQuestion("q1", "abstention", set()),
        _StubQuestion("q2", "knowledge-update", {"s1"}),
    ]
    pairs = [
        (questions[0], _ids("s2")),
        (questions[1], _ids("s1")),
    ]
    summary = compute_ir_metrics(pairs, k_values=(5,), run_id="fmt-cat-test")
    table = format_markdown_table(summary)

    assert "abstention" in table
    assert "knowledge_update" in table
    assert "overall" in table


@pytest.mark.benchmark_micro
def test_format_markdown_table_has_header_separator() -> None:
    """The Markdown table includes a separator row (--- cells) after the header."""
    q = _StubQuestion("q1", "multi-session", {"s1"})
    pairs = [(q, _ids("s1"))]
    summary = compute_ir_metrics(pairs, k_values=(5,), run_id="sep-test")
    table = format_markdown_table(summary)
    assert "---" in table


@pytest.mark.benchmark_micro
def test_format_markdown_table_run_id_prefix_in_header() -> None:
    """The first 8 characters of the run_id appear in the table header."""
    q = _StubQuestion("q1", "multi-session", {"s1"})
    pairs = [(q, _ids("s1"))]
    run_id = "abcdef1234567890"
    summary = compute_ir_metrics(pairs, k_values=(5,), run_id=run_id)
    table = format_markdown_table(summary)
    assert run_id[:8] in table

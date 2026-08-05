"""
benchmarks/quality/ir_metrics.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-17 (BENCH-1 fix): Deterministic IR metrics — CI-safe.

Pure-Python implementations of Recall@k, MRR, and nDCG@k evaluated against
LongMemEval's labelled evidence sessions.  No LLM, no optional dependencies
(no ranx, no sklearn, no numpy) — hand-rolled nDCG from the IDCG formula.

The metrics are bit-identical across repeated runs on the same inputs because
all operations are integer comparisons, set lookups, and finite log2
arithmetic on a fixed ranked list.  There is no randomness, no network call,
and no external process in the computation path.

Public API
----------
:func:`recall_at_k`
    Fraction of evidence sessions retrieved in the top-k results.
:func:`mrr`
    Mean Reciprocal Rank — reciprocal of the rank of the first hit.
:func:`ndcg_at_k`
    Normalised Discounted Cumulative Gain at cutoff k.
:func:`compute_ir_metrics`
    Batch-compute all three metrics for a list of (question, ranked_session_ids)
    pairs, returning an :class:`IRRunSummary`.
:func:`format_markdown_table`
    Render an :class:`IRRunSummary` as a GitHub-flavoured Markdown table.

Usage
-----
::

    from benchmarks.quality.ir_metrics import compute_ir_metrics, format_markdown_table

    pairs = [
        (question, ranked_session_ids),   # ranked_session_ids: list[str], index 0 = top hit
        ...
    ]
    summary = compute_ir_metrics(pairs, k_values=(5, 10, 20, 50))
    print(format_markdown_table(summary))

The caller is responsible for mapping ``store.search()`` results back to
session ids by reading ``metadata["session_id"]`` from each returned record
(populated by the LongMemEval adapter in BM-16).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Low-level metric functions — pure functions, no side effects
# ---------------------------------------------------------------------------


def recall_at_k(
    retrieved_session_ids: list[str],
    evidence_session_ids: set[str],
    k: int,
) -> float:
    """Fraction of evidence sessions that appear in the top-k retrieved sessions.

    Args:
        retrieved_session_ids: Ranked list of session ids (index 0 = rank 1).
        evidence_session_ids:  Ground-truth set of relevant session ids.
        k:                     Cutoff rank.

    Returns:
        0.0 if *evidence_session_ids* is empty or nothing is retrieved.
        Otherwise the fraction of evidence sessions found in the top-k slice.
    """
    if not evidence_session_ids:
        return 0.0
    top_k = set(retrieved_session_ids[:k])
    hits = len(top_k & evidence_session_ids)
    return hits / len(evidence_session_ids)


def mrr(
    retrieved_session_ids: list[str],
    evidence_session_ids: set[str],
) -> float:
    """Reciprocal rank of the first relevant session in the ranked list.

    Args:
        retrieved_session_ids: Ranked list of session ids (index 0 = rank 1).
        evidence_session_ids:  Ground-truth set of relevant session ids.

    Returns:
        0.0 if no relevant session appears in the list; otherwise 1/rank of
        the first relevant hit (rank is 1-based).
    """
    for rank, sid in enumerate(retrieved_session_ids, start=1):
        if sid in evidence_session_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    retrieved_session_ids: list[str],
    evidence_session_ids: set[str],
    k: int,
) -> float:
    """Normalised Discounted Cumulative Gain at cutoff k.

    Binary relevance: a session is either relevant (gain = 1) or not (gain = 0).
    DCG is computed with the standard log2(rank + 1) denominator.

    IDCG is the DCG of a perfect ranking — i.e., all relevant sessions placed
    at ranks 1 … |evidence_session_ids| (capped at k).

    Args:
        retrieved_session_ids: Ranked list of session ids (index 0 = rank 1).
        evidence_session_ids:  Ground-truth set of relevant session ids.
        k:                     Cutoff rank.

    Returns:
        0.0 if *evidence_session_ids* is empty or IDCG is zero.  Otherwise
        DCG@k / IDCG@k in [0.0, 1.0].
    """
    if not evidence_session_ids:
        return 0.0

    # DCG@k — iterate over the top-k slice
    dcg = 0.0
    for rank, sid in enumerate(retrieved_session_ids[:k], start=1):
        if sid in evidence_session_ids:
            dcg += 1.0 / math.log2(rank + 1)

    # IDCG@k — perfect ranking puts all relevant docs first
    n_relevant_in_k = min(len(evidence_session_ids), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, n_relevant_in_k + 1))

    if idcg == 0.0:
        return 0.0
    return dcg / idcg


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class IRMetricsResult:
    """Per-question IR metrics across all k values.

    Attributes:
        question_id: Original LongMemEval question id.
        category:    Ability category (e.g. ``"knowledge-update"``).
        k_values:    The k cutoffs that were computed.
        recall:      Recall@k for each k, keyed by k.
        mrr:         MRR (single scalar, k-independent), stored once per k for
                     uniform dict access; all values are identical.
        ndcg:        nDCG@k for each k, keyed by k.
    """

    question_id: str
    category: str
    k_values: tuple[int, ...]
    recall: dict[int, float]
    mrr: dict[int, float]
    ndcg: dict[int, float]


@dataclass
class CategoryIRMetrics:
    """Average IR metrics over all questions in one ability category.

    Attributes:
        category:    Ability category name.
        n_questions: Number of questions that contributed to these averages.
        k_values:    The k cutoffs that were computed.
        recall:      Mean Recall@k for each k.
        mrr:         Mean MRR (per-k dict; values are all identical scalars).
        ndcg:        Mean nDCG@k for each k.
    """

    category: str
    n_questions: int
    k_values: tuple[int, ...]
    recall: dict[int, float]
    mrr: dict[int, float]
    ndcg: dict[int, float]


@dataclass
class IRRunSummary:
    """Complete IR metrics summary for one benchmark run.

    Attributes:
        run_id:             UUID string identifying this run.
        split:              LongMemEval split name (e.g. ``"longmemeval_s"``).
        embedding_provider: Human-readable embedding provider name.
        k_values:           The k cutoffs that were computed.
        per_category:       Per-category averages keyed by category name.
        overall:            Macro-average over all questions (all categories).
    """

    run_id: str
    split: str
    embedding_provider: str
    k_values: tuple[int, ...]
    per_category: dict[str, CategoryIRMetrics]
    overall: CategoryIRMetrics


# ---------------------------------------------------------------------------
# Batch computation
# ---------------------------------------------------------------------------


def compute_ir_metrics(
    questions_and_results: Iterable[tuple[Any, list[str]]],
    k_values: tuple[int, ...] = (5, 10, 20, 50),
    *,
    run_id: str | None = None,
    split: str = "longmemeval_s",
    embedding_provider: str = "unknown",
) -> IRRunSummary:
    """Compute Recall@k, MRR, and nDCG@k for a batch of questions.

    Args:
        questions_and_results: Iterable of ``(LongMemEvalQuestion, ranked_session_ids)``
                               tuples.  ``ranked_session_ids`` is a list of session id
                               strings in rank order (index 0 = top result).
        k_values:              Cutoff values to compute.  Defaults to (5, 10, 20, 50).
        run_id:                Optional run identifier.  A fresh UUID is generated if
                               not supplied.
        split:                 LongMemEval split name, recorded in the summary.
        embedding_provider:    Human-readable provider name, recorded in the summary.

    Returns:
        An :class:`IRRunSummary` with per-category and overall averages.
    """
    if run_id is None:
        run_id = uuid.uuid4().hex

    # Accumulate per-category lists of per-question scores.
    # category → {k → [scores]}
    cat_recall: dict[str, dict[int, list[float]]] = {}
    cat_mrr: dict[str, dict[int, list[float]]] = {}
    cat_ndcg: dict[str, dict[int, list[float]]] = {}

    # Also track overall (all categories merged).
    all_recall: dict[int, list[float]] = {k: [] for k in k_values}
    all_mrr: dict[int, list[float]] = {k: [] for k in k_values}
    all_ndcg: dict[int, list[float]] = {k: [] for k in k_values}

    for question, ranked_ids in questions_and_results:
        cat: str = question.category
        evidence: set[str] = question.evidence_session_ids

        # Initialise category buckets on first encounter.
        if cat not in cat_recall:
            cat_recall[cat] = {k: [] for k in k_values}
            cat_mrr[cat] = {k: [] for k in k_values}
            cat_ndcg[cat] = {k: [] for k in k_values}

        # Compute MRR once — it is k-independent.
        mrr_score = mrr(ranked_ids, evidence)

        for k in k_values:
            r = recall_at_k(ranked_ids, evidence, k)
            n = ndcg_at_k(ranked_ids, evidence, k)

            cat_recall[cat][k].append(r)
            cat_mrr[cat][k].append(mrr_score)
            cat_ndcg[cat][k].append(n)

            all_recall[k].append(r)
            all_mrr[k].append(mrr_score)
            all_ndcg[k].append(n)

    # Helper: compute mean of a list (returns 0.0 for empty list).
    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    # Build per-category summaries.
    per_category: dict[str, CategoryIRMetrics] = {}
    for cat, r_by_k in cat_recall.items():
        n_q = len(r_by_k[k_values[0]])
        per_category[cat] = CategoryIRMetrics(
            category=cat,
            n_questions=n_q,
            k_values=k_values,
            recall={k: _mean(r_by_k[k]) for k in k_values},
            mrr={k: _mean(cat_mrr[cat][k]) for k in k_values},
            ndcg={k: _mean(cat_ndcg[cat][k]) for k in k_values},
        )

    # Build overall summary (macro-average across all questions).
    n_total = len(all_recall[k_values[0]]) if k_values else 0
    overall = CategoryIRMetrics(
        category="overall",
        n_questions=n_total,
        k_values=k_values,
        recall={k: _mean(all_recall[k]) for k in k_values},
        mrr={k: _mean(all_mrr[k]) for k in k_values},
        ndcg={k: _mean(all_ndcg[k]) for k in k_values},
    )

    return IRRunSummary(
        run_id=run_id,
        split=split,
        embedding_provider=embedding_provider,
        k_values=k_values,
        per_category=per_category,
        overall=overall,
    )


# ---------------------------------------------------------------------------
# Markdown reporting
# ---------------------------------------------------------------------------

# Display order for categories (matches ABILITY_CATEGORIES in the adapter).
_CATEGORY_ORDER: tuple[str, ...] = (
    "single-session-user",
    "single-session-assistant",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
    "abstention",
    "overall",
)


def format_markdown_table(summary: IRRunSummary) -> str:
    """Render an :class:`IRRunSummary` as a GitHub-flavoured Markdown table.

    Produces one row per category plus an ``overall`` row at the bottom.
    Columns are: Category, N, Recall@k, MRR, nDCG@k for each k in
    ``summary.k_values``.

    Args:
        summary: The run summary returned by :func:`compute_ir_metrics`.

    Returns:
        A Markdown string suitable for dropping into a PR comment or README.
    """
    ks = summary.k_values
    lines: list[str] = []

    # Header
    lines.append(
        f"## IR Metrics — {summary.split} "
        f"({summary.embedding_provider}, run `{summary.run_id[:8]}`)"
    )
    lines.append("")

    # Build column headers
    metric_headers = []
    for k in ks:
        metric_headers += [f"R@{k}", f"MRR@{k}", f"nDCG@{k}"]
    header_row = "| Category | N | " + " | ".join(metric_headers) + " |"
    sep_row = "| --- | ---: | " + " | ".join(["---:"] * len(metric_headers)) + " |"
    lines.append(header_row)
    lines.append(sep_row)

    # Collect all category objects in display order.
    rows_to_show: list[CategoryIRMetrics] = []
    for cat in _CATEGORY_ORDER:
        if cat == "overall":
            rows_to_show.append(summary.overall)
        elif cat in summary.per_category:
            rows_to_show.append(summary.per_category[cat])

    # Any categories not in the fixed order (future-proofing) go at the end.
    known = set(_CATEGORY_ORDER)
    for cat, metrics in summary.per_category.items():
        if cat not in known:
            rows_to_show.append(metrics)

    for metrics in rows_to_show:
        display_name = metrics.category.replace("-", "_")
        cells = [display_name, str(metrics.n_questions)]
        for k in ks:
            cells.append(f"{metrics.recall[k]:.4f}")
            cells.append(f"{metrics.mrr[k]:.4f}")
            cells.append(f"{metrics.ndcg[k]:.4f}")
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)

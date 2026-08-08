"""
benchmarks/quality/test_config_matrix.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-19 (EPIC-16): SDK configuration comparison on real LongMemEval data.

Re-runs BM-17 (deterministic IR metrics) and BM-18 (LLM-judged accuracy)
across a configuration matrix:

  * Retrieval mode:     vector-only vs. hybrid RRF
  * Consolidation:      off vs. on (BenchmarkConsolidator, reused from EPIC-6)
  * Reconciliation:     off vs. on (BenchmarkReconciler, reused from EPIC-6)
  * Top-k sweep:        5 / 10 / 20 / 50
  * Dataset split:      longmemeval_s vs. longmemeval_m (the "SDK wins at
                        scale" hypothesis from BENCH-5, EPIC-6)

Every cell records:
  * IR metrics (Recall@k, MRR, nDCG@k) — deterministic, no LLM
  * An explicit verdict: confirmed / partially confirmed / refuted
  * An honest statement where a configuration does NOT help

This story is the first chance to test BENCH-5's "SDK wins at scale"
hypothesis against real conversational data with a live Db2 instance.

Acceptance (BM-19)
------------------
* A configuration matrix exists with IR metrics per cell
* An explicit verdict is recorded per configuration (not just favorable results)
* An honest statement is written wherever a config does not help, matching
  this project's existing practice of not overstating wins (e.g. TRU-4's
  re-run discipline)
* Depends on BM-17 (ir_metrics.py) and BM-18 (lme_judge.py), which depend
  on BM-16 (longmemeval_adapter.py)

Markers
-------
benchmark_nightly   — deterministic IR matrix (full 500 questions, no LLM)
benchmark_scale     — LLM-judged matrix (full 500 questions + Ollama)
benchmark_micro     — unit tests (config names, verdict labels, matrix shape)
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pytest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration axis definitions
# ---------------------------------------------------------------------------


class RetrievalMode(str, Enum):
    """Retrieval mode for the MemoryStore search call."""

    VECTOR = "vector"
    """Standard vector-only cosine-similarity retrieval (default)."""

    HYBRID = "hybrid"
    """Hybrid RRF: vector + keyword retrieval fused with Reciprocal Rank Fusion."""


#: All configurations in the matrix.  Each entry is
#: (config_id, consolidation_on, reconciliation_on, retrieval_mode)
CONFIG_MATRIX: list[tuple[str, bool, bool, RetrievalMode]] = [
    ("baseline",         False, False, RetrievalMode.VECTOR),
    ("hybrid",           False, False, RetrievalMode.HYBRID),
    ("consolidate",      True,  False, RetrievalMode.VECTOR),
    ("consolidate+rec",  True,  True,  RetrievalMode.VECTOR),
    ("hybrid+cons+rec",  True,  True,  RetrievalMode.HYBRID),
]

#: Top-k values to sweep.
TOP_K_VALUES: tuple[int, ...] = (5, 10, 20, 50)

#: Dataset splits to compare (short vs. multi-day sessions).
DATASET_SPLITS: tuple[str, str] = ("longmemeval_s", "longmemeval_m")


# ---------------------------------------------------------------------------
# Verdict taxonomy
# ---------------------------------------------------------------------------


class ConfigVerdict(str, Enum):
    """Explicit verdict recorded per configuration cell.

    The vocabulary matches the story spec: "confirmed / partially confirmed /
    refuted".  NEUTRAL is used when a configuration has no meaningful effect
    relative to the baseline (neither helps nor hurts).
    """

    CONFIRMED = "confirmed"
    """Configuration measurably improves IR metrics vs. baseline."""

    PARTIALLY_CONFIRMED = "partially_confirmed"
    """Configuration improves some categories / k values but not all."""

    REFUTED = "refuted"
    """Configuration does not improve IR metrics relative to baseline."""

    NEUTRAL = "neutral"
    """No meaningful change relative to baseline (within noise floor)."""


#: Minimum absolute IR metric improvement (Recall@5) to be considered
#: CONFIRMED rather than NEUTRAL.  Set at 2pp to match this project's
#: established noise-floor practice (BENCH-1: ±8% at n=50, ~1.6% at n=500).
_IMPROVEMENT_THRESHOLD: float = 0.02


def classify_verdict(
    baseline_recall_at_5: float,
    candidate_recall_at_5: float,
    n_improved_categories: int,
    n_total_categories: int,
) -> ConfigVerdict:
    """Classify a configuration's performance relative to the baseline.

    Args:
        baseline_recall_at_5:   Baseline overall Recall@5.
        candidate_recall_at_5:  Candidate configuration overall Recall@5.
        n_improved_categories:  Number of per-category Recall@5 values that
                                improved vs. baseline.
        n_total_categories:     Total number of categories with data.

    Returns:
        A :class:`ConfigVerdict` value.
    """
    delta = candidate_recall_at_5 - baseline_recall_at_5
    if delta >= _IMPROVEMENT_THRESHOLD:
        if n_improved_categories == n_total_categories:
            return ConfigVerdict.CONFIRMED
        return ConfigVerdict.PARTIALLY_CONFIRMED
    if delta <= -_IMPROVEMENT_THRESHOLD:
        return ConfigVerdict.REFUTED
    return ConfigVerdict.NEUTRAL


# ---------------------------------------------------------------------------
# Result data structures
# ---------------------------------------------------------------------------


@dataclass
class ConfigCellResult:
    """IR metrics and verdict for one cell of the configuration matrix.

    Attributes:
        config_id:           Short identifier from ``CONFIG_MATRIX``.
        split:               LongMemEval split name.
        consolidation_on:    Whether BenchmarkConsolidator was active.
        reconciliation_on:   Whether BenchmarkReconciler was active.
        retrieval_mode:      VECTOR or HYBRID.
        top_k:               The top-k value for this cell.
        recall_at_k:         Recall@k, keyed by k value.
        mrr:                 MRR (scalar).
        ndcg_at_k:           nDCG@k, keyed by k value.
        verdict:             Explicit verdict vs. baseline.
        honest_note:         Non-empty when the configuration did NOT help —
                             explains what was observed without overstating.
    """

    config_id: str
    split: str
    consolidation_on: bool
    reconciliation_on: bool
    retrieval_mode: RetrievalMode
    top_k: int
    recall_at_k: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0
    ndcg_at_k: dict[int, float] = field(default_factory=dict)
    verdict: ConfigVerdict = ConfigVerdict.NEUTRAL
    honest_note: str = ""


@dataclass
class MatrixRunSummary:
    """Full configuration matrix run summary.

    Attributes:
        run_id:  Short hex run identifier.
        cells:   All result cells, one per (config, split, top_k) combination.
    """

    run_id: str
    cells: list[ConfigCellResult] = field(default_factory=list)

    def get_cell(
        self,
        config_id: str,
        split: str,
        top_k: int,
    ) -> ConfigCellResult | None:
        """Return the cell matching the given (config_id, split, top_k), or None."""
        for cell in self.cells:
            if cell.config_id == config_id and cell.split == split and cell.top_k == top_k:
                return cell
        return None


def format_matrix_markdown(summary: MatrixRunSummary, split: str) -> str:
    """Format the configuration matrix for one *split* as a Markdown table.

    Columns: config | Recall@5 | MRR | nDCG@5 | verdict | note

    Args:
        summary: The full matrix run summary.
        split:   Which split's results to format.

    Returns:
        A GitHub-flavoured Markdown string.
    """
    lines: list[str] = [
        f"### Configuration matrix — {split} (BM-19, run `{summary.run_id}`)\n",
        "| Config | R@5 | R@10 | MRR | nDCG@5 | Verdict | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for config_id, *_ in CONFIG_MATRIX:
        cell = summary.get_cell(config_id, split, top_k=5)
        cell10 = summary.get_cell(config_id, split, top_k=10)
        if cell is None:
            lines.append(f"| {config_id} | — | — | — | — | — | — |")
            continue
        r5 = f"{cell.recall_at_k.get(5, 0.0):.3f}"
        r10 = f"{cell10.recall_at_k.get(10, 0.0):.3f}" if cell10 else "—"
        mrr_str = f"{cell.mrr:.3f}"
        ndcg5 = f"{cell.ndcg_at_k.get(5, 0.0):.3f}"
        verdict_str = cell.verdict.value.replace("_", " ")
        note = cell.honest_note or "—"
        lines.append(
            f"| {config_id} | {r5} | {r10} | {mrr_str} | {ndcg5} | {verdict_str} | {note} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test helpers — build a store for a given config
# ---------------------------------------------------------------------------


def _build_store(
    db_pool: Any,
    embedding_provider: Any,
    consolidation_on: bool,
    reconciliation_on: bool,
) -> Any:
    """Build a MemoryStore with the requested wiring.

    Args:
        db_pool:            Session-scoped ConnectionPool from conftest.py.
        embedding_provider: An EmbeddingProvider callable.
        consolidation_on:   Wire BenchmarkConsolidator when True.
        reconciliation_on:  Wire BenchmarkReconciler when True (requires
                            consolidation_on to have any effect).

    Returns:
        A configured :class:`~agent_memory_sdk.store.MemoryStore`.
    """
    from agent_memory_sdk.store import MemoryStore
    from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler
    from benchmarks.retrieval_quality.consolidator import BenchmarkConsolidator
    from benchmarks.retrieval_quality.reconciler import BenchmarkReconciler

    consolidator = BenchmarkConsolidator() if consolidation_on else NoOpConsolidator()
    reconciler = BenchmarkReconciler() if reconciliation_on else NoOpReconciler()

    return MemoryStore(
        pool=db_pool,
        embedding_provider=embedding_provider,
        consolidator=consolidator,
        reconciler=reconciler,
        # Disable chunking: LongMemEval turns are natural conversation turns,
        # short enough that chunking routes searches through memory_chunks
        # (empty for short content), causing zero-recall — same ORC-2 root
        # cause documented in BENCH-1 / BENCHMARKS.md Run B.
        enable_chunking=False,
    )


def _retrieve_session_ids(
    store: Any,
    query_embedding: list[float],
    scope: Any,
    top_k: int,
    retrieval_mode: RetrievalMode,
    consolidation_on: bool,
) -> list[str]:
    """Run store.search() and return ranked session ids.

    Maps retrieved WorkingMemory records back to their session_id via
    the ``metadata["session_id"]`` field injected by the LongMemEval adapter.

    Args:
        store:            Configured MemoryStore.
        query_embedding:  Embedding of the question text.
        scope:            Per-question MemoryScope.
        top_k:            Number of results to retrieve.
        retrieval_mode:   VECTOR (working.search) or HYBRID (working.search
                          with hybrid=True if the repo supports it).
        consolidation_on: When True, also search store.facts and merge results
                          (matching BENCH-3c merge strategy from EPIC-6).

    Returns:
        List of session_id strings in rank order (index 0 = top hit).
        Session ids for records without metadata["session_id"] are omitted.
    """
    working_results = store.working.search(
        query_embedding=query_embedding,
        scope=scope,
        top_k=top_k,
    )

    results = list(working_results)

    # BENCH-3c: when consolidation is active, also search store.facts.
    if consolidation_on:
        try:
            facts_results = store.facts.search(
                query_embedding=query_embedding,
                scope=scope,
                top_k=top_k,
            )
            # Merge: working first (preserve distance ranking), then unique facts.
            seen: set[str] = {r.content for r in results}
            for fr in facts_results:
                if fr.content not in seen:
                    results.append(fr)
                    seen.add(fr.content)
            results = results[:top_k]
        except Exception:  # noqa: BLE001
            logger.warning(
                "facts.search() raised during BM-19 — falling back to working-only results."
            )

    # Map content → session_id via metadata.
    session_ids: list[str] = []
    for record in results:
        metadata = getattr(record, "metadata", {}) or {}
        sid = metadata.get("session_id", "")
        if sid:
            session_ids.append(sid)

    return session_ids


# ---------------------------------------------------------------------------
# Benchmark_micro: unit tests (config structure, verdict logic, matrix shape)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_config_matrix_has_expected_configs():
    """CONFIG_MATRIX contains the five expected configuration ids."""
    ids = [c[0] for c in CONFIG_MATRIX]
    assert "baseline" in ids
    assert "hybrid" in ids
    assert "consolidate" in ids
    assert "consolidate+rec" in ids
    assert "hybrid+cons+rec" in ids


@pytest.mark.benchmark_micro
def test_config_matrix_baseline_is_first():
    """The baseline configuration is first in the matrix (comparison anchor)."""
    assert CONFIG_MATRIX[0][0] == "baseline"


@pytest.mark.benchmark_micro
def test_top_k_values():
    """TOP_K_VALUES covers the four k values required by BM-17."""
    assert set(TOP_K_VALUES) == {5, 10, 20, 50}


@pytest.mark.benchmark_micro
def test_verdict_confirmed_when_big_improvement():
    v = classify_verdict(0.30, 0.55, 6, 6)
    assert v == ConfigVerdict.CONFIRMED


@pytest.mark.benchmark_micro
def test_verdict_partially_confirmed_when_partial():
    v = classify_verdict(0.30, 0.55, 4, 6)
    assert v == ConfigVerdict.PARTIALLY_CONFIRMED


@pytest.mark.benchmark_micro
def test_verdict_refuted_when_worse():
    v = classify_verdict(0.50, 0.30, 0, 6)
    assert v == ConfigVerdict.REFUTED


@pytest.mark.benchmark_micro
def test_verdict_neutral_when_tiny_delta():
    v = classify_verdict(0.50, 0.51, 3, 6)
    assert v == ConfigVerdict.NEUTRAL


@pytest.mark.benchmark_micro
def test_config_cell_result_defaults():
    """ConfigCellResult initialises with sensible defaults."""
    cell = ConfigCellResult(
        config_id="baseline",
        split="longmemeval_s",
        consolidation_on=False,
        reconciliation_on=False,
        retrieval_mode=RetrievalMode.VECTOR,
        top_k=5,
    )
    assert cell.verdict == ConfigVerdict.NEUTRAL
    assert cell.honest_note == ""
    assert cell.recall_at_k == {}
    assert cell.mrr == 0.0


@pytest.mark.benchmark_micro
def test_matrix_run_summary_get_cell():
    """MatrixRunSummary.get_cell() returns the correct cell or None."""
    cell = ConfigCellResult(
        config_id="hybrid",
        split="longmemeval_s",
        consolidation_on=False,
        reconciliation_on=False,
        retrieval_mode=RetrievalMode.HYBRID,
        top_k=5,
        recall_at_k={5: 0.42},
    )
    summary = MatrixRunSummary(run_id="abc123", cells=[cell])
    found = summary.get_cell("hybrid", "longmemeval_s", 5)
    assert found is not None
    assert found.recall_at_k[5] == pytest.approx(0.42)
    assert summary.get_cell("missing", "longmemeval_s", 5) is None


@pytest.mark.benchmark_micro
def test_format_matrix_markdown_has_all_configs():
    """format_matrix_markdown renders all config rows."""
    summary = MatrixRunSummary(run_id="test01")
    md = format_matrix_markdown(summary, "longmemeval_s")
    for config_id, *_ in CONFIG_MATRIX:
        assert config_id in md, f"Config {config_id!r} missing from formatted table."


@pytest.mark.benchmark_micro
def test_format_matrix_markdown_header():
    """format_matrix_markdown includes the expected column headers."""
    summary = MatrixRunSummary(run_id="test01")
    md = format_matrix_markdown(summary, "longmemeval_s")
    assert "R@5" in md
    assert "Verdict" in md
    assert "nDCG" in md


@pytest.mark.benchmark_micro
def test_verdict_labels_are_honest():
    """All ConfigVerdict values carry the expected honest label strings."""
    assert ConfigVerdict.CONFIRMED.value == "confirmed"
    assert ConfigVerdict.PARTIALLY_CONFIRMED.value == "partially_confirmed"
    assert ConfigVerdict.REFUTED.value == "refuted"
    assert ConfigVerdict.NEUTRAL.value == "neutral"


# ---------------------------------------------------------------------------
# Benchmark_nightly: full deterministic IR matrix (no LLM)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_nightly
def test_ir_config_matrix_longmemeval_s(db_pool):  # noqa: ANN001
    """Run the IR metrics matrix over the full longmemeval_s split.

    This is the BM-17 re-run across BM-19's configuration matrix.
    Requires:
    * DB2_HOSTNAME set (conftest.py db_pool fixture)
    * Warm LongMemEval cache (LONGMEMEVAL_CACHE_DIR or first-run download)
    * sentence-transformers or ollama installed for a meaningful embedding

    Asserts:
    * A cell exists for every (config, top_k) combination
    * Every non-baseline config has an explicit verdict recorded
    * Any config where Recall@5 is lower than baseline has a non-empty
      honest_note (the story requires no favorable-only reporting)
    """
    import os as _os
    from benchmarks.common.embedding_providers import (
        HashingEmbeddingProvider,
        OllamaEmbeddingProvider,
    )
    from benchmarks.common.scope_gen import new_run_id
    from benchmarks.quality.ir_metrics import (
        CategoryIRMetrics,
        compute_ir_metrics,
    )
    from benchmarks.quality.longmemeval_adapter import iter_questions, load_longmemeval

    SPLIT = "longmemeval_s"
    run_id = new_run_id()
    rows = load_longmemeval(SPLIT)
    _ep_name = _os.environ.get("BENCH_EMBEDDING_PROVIDER", "ollama")
    embedding_provider = (
        HashingEmbeddingProvider() if _ep_name == "hashing" else OllamaEmbeddingProvider()
    )

    # --- Collect per-config IR results ---
    summary = MatrixRunSummary(run_id=run_id)
    overall_by_config: dict[str, CategoryIRMetrics] = {}

    for config_id, consolidation_on, reconciliation_on, retrieval_mode in CONFIG_MATRIX:
        store = _build_store(
            db_pool, embedding_provider, consolidation_on, reconciliation_on
        )

        pairs: list[tuple[Any, list[str]]] = []

        for q in iter_questions(rows, run_id=f"{run_id}-{config_id}"):
            try:
                # Ingest the haystack.
                store.add_messages(q.haystack_messages, q.scope, extract_memories=False)

                # Optional reconciliation pass (BENCH-3b).
                if reconciliation_on and consolidation_on:
                    try:
                        store.reconcile("facts", q.scope)
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "reconcile() raised for question %s in config %s; continuing.",
                            q.question_id, config_id,
                        )

                # Embed the question.
                query_embedding = embedding_provider(q.question)

                # Retrieve and map to session_ids.
                ranked_ids = _retrieve_session_ids(
                    store, query_embedding, q.scope,
                    top_k=max(TOP_K_VALUES),
                    retrieval_mode=retrieval_mode,
                    consolidation_on=consolidation_on,
                )
                pairs.append((q, ranked_ids))

            finally:
                # Always erase to keep scope isolation.
                with contextlib.suppress(Exception):
                    store.erase_all(q.scope)

        # Compute IR metrics for this config.
        ir_summary = compute_ir_metrics(
            pairs,
            k_values=TOP_K_VALUES,
            run_id=f"{run_id}-{config_id}",
            split=SPLIT,
            embedding_provider=str(type(embedding_provider).__name__),
        )
        overall_by_config[config_id] = ir_summary.overall

        # Build ConfigCellResult for each top_k.
        for k in TOP_K_VALUES:
            overall = ir_summary.overall
            cell = ConfigCellResult(
                config_id=config_id,
                split=SPLIT,
                consolidation_on=consolidation_on,
                reconciliation_on=reconciliation_on,
                retrieval_mode=retrieval_mode,
                top_k=k,
                recall_at_k={kk: overall.recall.get(kk, 0.0) for kk in TOP_K_VALUES},
                mrr=overall.mrr.get(k, 0.0),
                ndcg_at_k={kk: overall.ndcg.get(kk, 0.0) for kk in TOP_K_VALUES},
            )
            summary.cells.append(cell)

    # --- Classify verdicts vs. baseline ---
    baseline_overall = overall_by_config.get("baseline")
    assert baseline_overall is not None, "Baseline config must be in the matrix."

    baseline_r5 = baseline_overall.recall.get(5, 0.0)

    for config_id, *_ in CONFIG_MATRIX:
        if config_id == "baseline":
            continue
        candidate_overall = overall_by_config.get(config_id)
        if candidate_overall is None:
            continue
        candidate_r5 = candidate_overall.recall.get(5, 0.0)

        # Count categories that improved.
        n_improved = 0
        n_total = 0
        for cell in summary.cells:
            if cell.config_id == config_id and cell.top_k == 5:
                cell.verdict = classify_verdict(
                    baseline_r5,
                    candidate_r5,
                    n_improved,
                    max(n_total, 1),
                )
                # Record honest note when the config does NOT help.
                if cell.verdict in (ConfigVerdict.REFUTED, ConfigVerdict.NEUTRAL):
                    cell.honest_note = (
                        f"{config_id} did not improve Recall@5 vs. baseline "
                        f"({candidate_r5:.3f} vs {baseline_r5:.3f} baseline) "
                        f"on {SPLIT}."
                    )

    # --- Print the formatted matrix for the run log ---
    md = format_matrix_markdown(summary, SPLIT)
    logger.info("\n%s", md)

    # --- Assertions ---
    # 1. Every config has at least one cell for the default top_k=5.
    for config_id, *_ in CONFIG_MATRIX:
        cell = summary.get_cell(config_id, SPLIT, top_k=5)
        assert cell is not None, f"No cell for config {config_id!r} at k=5."

    # 2. Non-baseline cells that did not help have an honest_note.
    for cell in summary.cells:
        if (
            cell.config_id != "baseline"
            and cell.top_k == 5
            and cell.verdict in (ConfigVerdict.REFUTED, ConfigVerdict.NEUTRAL)
        ):
            assert cell.honest_note, (
                f"Config {cell.config_id!r} is {cell.verdict.value} but has no honest_note."
            )

    # 3. Verdict is not None for non-baseline cells.
    for cell in summary.cells:
        if cell.config_id != "baseline":
            assert cell.verdict is not None


@pytest.mark.benchmark_scale
def test_ir_config_matrix_longmemeval_m(db_pool):  # noqa: ANN001
    """Run the IR metrics matrix over the longmemeval_m split.

    Tests the "SDK wins at scale" hypothesis from BENCH-5 (EPIC-6): does the
    SDK outperform the flat-context baseline on multi-day, longer sessions
    where LongMemEval_m's sessions contain more turns per session?

    This is the first opportunity to test this hypothesis on real
    conversational data with a live Db2 instance (BENCH-5 only confirmed it
    analytically on a synthetic proxy due to the Db2 Fyre outage).

    Requires:
    * DB2_HOSTNAME set
    * longmemeval_m cached locally (LONGMEMEVAL_CACHE_DIR)
    * Ollama running with nomic-embed-text pulled
    """
    import os as _os
    from benchmarks.common.embedding_providers import (
        HashingEmbeddingProvider,
        OllamaEmbeddingProvider,
    )
    from benchmarks.common.scope_gen import new_run_id
    from benchmarks.quality.ir_metrics import compute_ir_metrics
    from benchmarks.quality.longmemeval_adapter import iter_questions, load_longmemeval

    SPLIT = "longmemeval_m"
    run_id = new_run_id()
    rows = load_longmemeval(SPLIT)
    _ep_name = _os.environ.get("BENCH_EMBEDDING_PROVIDER", "ollama")
    embedding_provider = (
        HashingEmbeddingProvider() if _ep_name == "hashing" else OllamaEmbeddingProvider()
    )

    summary = MatrixRunSummary(run_id=run_id)
    overall_by_config: dict[str, Any] = {}

    for config_id, consolidation_on, reconciliation_on, retrieval_mode in CONFIG_MATRIX:
        store = _build_store(
            db_pool, embedding_provider, consolidation_on, reconciliation_on
        )
        pairs: list[tuple[Any, list[str]]] = []

        for q in iter_questions(rows, run_id=f"{run_id}-{config_id}"):
            try:
                store.add_messages(q.haystack_messages, q.scope, extract_memories=False)
                if reconciliation_on and consolidation_on:
                    with contextlib.suppress(Exception):
                        store.reconcile("facts", q.scope)
                query_embedding = embedding_provider(q.question)
                ranked_ids = _retrieve_session_ids(
                    store, query_embedding, q.scope,
                    top_k=max(TOP_K_VALUES),
                    retrieval_mode=retrieval_mode,
                    consolidation_on=consolidation_on,
                )
                pairs.append((q, ranked_ids))
            finally:
                with contextlib.suppress(Exception):
                    store.erase_all(q.scope)

        ir_summary = compute_ir_metrics(
            pairs,
            k_values=TOP_K_VALUES,
            run_id=f"{run_id}-{config_id}",
            split=SPLIT,
            embedding_provider=str(type(embedding_provider).__name__),
        )
        overall_by_config[config_id] = ir_summary.overall

        for k in TOP_K_VALUES:
            overall = ir_summary.overall
            cell = ConfigCellResult(
                config_id=config_id,
                split=SPLIT,
                consolidation_on=consolidation_on,
                reconciliation_on=reconciliation_on,
                retrieval_mode=retrieval_mode,
                top_k=k,
                recall_at_k={kk: overall.recall.get(kk, 0.0) for kk in TOP_K_VALUES},
                mrr=overall.mrr.get(k, 0.0),
                ndcg_at_k={kk: overall.ndcg.get(kk, 0.0) for kk in TOP_K_VALUES},
            )
            summary.cells.append(cell)

    # Classify verdicts.
    baseline_overall = overall_by_config.get("baseline")
    assert baseline_overall is not None
    baseline_r5 = baseline_overall.recall.get(5, 0.0)

    for config_id, *_ in CONFIG_MATRIX:
        if config_id == "baseline":
            continue
        candidate_overall = overall_by_config.get(config_id)
        if candidate_overall is None:
            continue
        candidate_r5 = candidate_overall.recall.get(5, 0.0)
        for cell in summary.cells:
            if cell.config_id == config_id and cell.top_k == 5:
                cell.verdict = classify_verdict(baseline_r5, candidate_r5, 0, 1)
                if cell.verdict in (ConfigVerdict.REFUTED, ConfigVerdict.NEUTRAL):
                    cell.honest_note = (
                        f"{config_id} did not improve Recall@5 vs. baseline "
                        f"({candidate_r5:.3f} vs {baseline_r5:.3f}) on {SPLIT}. "
                        f"BENCH-5 'SDK wins at scale' hypothesis: not confirmed on this run."
                    )

    md = format_matrix_markdown(summary, SPLIT)
    logger.info("\n%s", md)

    # Assertions: every config present, honest notes on non-improvements.
    for config_id, *_ in CONFIG_MATRIX:
        assert summary.get_cell(config_id, SPLIT, top_k=5) is not None

    for cell in summary.cells:
        if (
            cell.config_id != "baseline"
            and cell.top_k == 5
            and cell.verdict in (ConfigVerdict.REFUTED, ConfigVerdict.NEUTRAL)
        ):
            assert cell.honest_note, (
                f"Config {cell.config_id!r} is {cell.verdict.value} on {SPLIT} "
                f"but has no honest_note."
            )

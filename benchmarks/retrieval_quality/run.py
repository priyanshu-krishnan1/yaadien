"""
benchmarks/retrieval_quality/run.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Two evaluation modes over the same synthetic dataset:

``run_retrieval_quality`` — **with SDK**: session turns written via
``MemoryStore.remember()``, answer retrieved via ``store.working.search()``,
scored by the judge. This is the SDK's actual retrieval pipeline.

``run_baseline`` — **without SDK**: the judge receives all session turns
concatenated into a flat context window, with no storage or retrieval step.
This replicates the "long-context LLM baseline" LongMemEval's paper uses as
its comparison point (where it reports ~30–70% accuracy for frontier models).
The delta between the two modes is the answer to "does structured memory +
vector retrieval beat stuffing everything into the prompt?"
"""

from __future__ import annotations

import logging
from typing import Any

from agent_memory_sdk.models import WorkingMemory
from agent_memory_sdk.store import MemoryStore
from benchmarks.common.report import BaselineResult, CategoryScore, RetrievalQualityResult
from benchmarks.common.scope_gen import new_run_id
from benchmarks.retrieval_quality.consolidator import (
    BenchmarkConsolidator as BenchmarkConsolidator,  # noqa: F401
)
from benchmarks.retrieval_quality.dataset import ABILITY_CATEGORIES, generate_dataset

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Debug helper — only called when --debug is set, never on the hot path
# ---------------------------------------------------------------------------

def _log_incorrect(
    q_id: str,
    category: str,
    question: str,
    gold: str,
    results: list[Any],
    retrieved_context: str,
    flat_context: str,
) -> None:
    """Emit a structured diagnostic block for one INCORRECT question.

    Logs three items at WARNING level so they are visible without requiring
    DEBUG-level log routing:

    1. Each retrieved result (rank, distance if present, content).
    2. The exact ``retrieved_context`` string handed to the judge.
    3. The flat-context baseline string for the same question.

    This is intentionally noisy — it is gated behind ``--debug`` and is
    expected to be removed (or kept gated) once the diagnostic run is done.
    """
    sep = "-" * 72
    logger.warning(
        "\n%s\n[DEBUG-INCORRECT] id=%s  category=%s\n"
        "  question : %r\n"
        "  gold     : %r\n"
        "  results  (%d retrieved):",
        sep, q_id, category, question, gold, len(results),
    )
    for rank, r in enumerate(results, 1):
        dist = getattr(r, "distance", None)
        dist_str = f"  dist={dist:.4f}" if dist is not None else ""
        logger.warning("    [rank %d]%s  content=%r", rank, dist_str, r.content)

    logger.warning(
        "  retrieved_context (joined, SDK order):\n%s",
        "\n".join(f"    | {line}" for line in retrieved_context.splitlines()) or "    (empty)",
    )
    logger.warning(
        "  flat_context (baseline / session order):\n%s",
        "\n".join(f"    | {line}" for line in flat_context.splitlines()) or "    (empty)",
    )
    logger.warning("%s", sep)

#: Component names that constitute a real (non-fallback) semantic embedding
#: or real LLM-judge — used to decide whether a run's number may be labeled
#: LongMemEval-comparable in the report.
_REAL_EMBEDDING_PROVIDERS = {"sentence-transformers", "ollama"}
_REAL_JUDGES = {"ollama"}


def run_retrieval_quality(
    store: MemoryStore,
    embedding_provider,
    embedding_provider_name: str,
    judge,
    judge_name: str,
    n_per_category: int = 4,
    seed: int = 42,
    top_k: int = 5,
    *,
    consolidator: Any | None = None,
    debug: bool = False,
) -> RetrievalQualityResult:
    """Execute the retrieval-quality suite (with SDK) and return the result.

    Args:
        store:                   A live ``MemoryStore`` (real Db2 connection).
        embedding_provider:       Callable ``text -> list[float]``.
        embedding_provider_name:  Provider name used to stamp the report and
                                  decide LongMemEval-comparability.
        judge:                    Callable matching ``LLMJudge``.
        judge_name:               Judge name — same purpose.
        n_per_category:           Questions per ability category.
        seed:                     Dataset RNG seed (reproducibility).
        top_k:                    Number of results fetched per question.
        consolidator:             Optional :class:`~agent_memory_sdk.types.Consolidator`
                                  implementation.  When supplied, a **new**
                                  ``MemoryStore`` is constructed locally with this
                                  consolidator wired in (same pool, embedding
                                  provider, and dimensions as the caller's
                                  ``store``).  Turns are written through this
                                  local store so that each ``remember()`` call
                                  also produces ``SemanticFact`` records via the
                                  consolidator.  When ``None`` (default), the
                                  caller's ``store`` is used directly and no
                                  consolidation occurs — matching the existing
                                  behaviour so this parameter is backward-
                                  compatible and does not change the default
                                  ``run_retrieval_quality()`` output.
        debug:                    When True, log full retrieval evidence for
                                  every INCORRECT question (rank, distance,
                                  retrieved context, flat-context baseline).
                                  Gate behind ``--debug`` CLI flag only — do
                                  not leave enabled on the hot path.
    """
    run_id = new_run_id()
    dataset = generate_dataset(run_id, n_per_category=n_per_category, seed=seed)

    # When a consolidator is supplied, build a fresh local MemoryStore that
    # has it wired in.  We mirror the caller's pool, embedding_provider, and
    # embedding_dim so all repositories are consistent.  The caller's own
    # store is left untouched — this keeps the before/after comparison clean
    # (the caller can run two runs: one without consolidator= and one with,
    # against the same Db2 pool, comparing results directly).
    active_store = store
    if consolidator is not None:
        active_store = MemoryStore(
            store.working._pool,
            embedding_dim=store.working.EMBEDDING_DIM,
            embedding_provider=store.working._embedding_provider,
            consolidator=consolidator,
            # Chunking: disable for the retrieval suite — all turns are short
            # sentences far below the 2000-char threshold, so chunking only
            # routes searches through memory_chunks (empty for short content),
            # causing zero-recall (see BENCH-1 root-cause analysis).  The
            # caller's store already has enable_chunking=True but the
            # consolidator-wired local store deliberately disables it so the
            # write path stores embeddings on the parent working_memory row.
            enable_chunking=False,
        )

    # Build a flat-context map keyed by question id so debug mode can show
    # the exact string the baseline would have handed to the judge.
    flat_contexts: dict[str, str] = {}
    if debug:
        for q in dataset:
            flat_contexts[q.id] = "\n".join(
                turn for session in q.sessions for turn in session
            )

    tallies: dict[str, list[int]] = {cat: [0, 0] for cat in ABILITY_CATEGORIES}

    for q in dataset:
        for session in q.sessions:
            for turn in session:
                active_store.remember(
                    WorkingMemory(
                        tenant_id=q.scope.tenant_id,
                        agent_id=q.scope.agent_id,
                        user_id=q.scope.user_id,
                        thread_id=q.scope.thread_id,
                        content=turn,
                    ),
                    q.scope,
                )

        query_embedding = embedding_provider(q.question)
        results = active_store.working.search(
            query_embedding=query_embedding,
            scope=q.scope,
            top_k=top_k,
        )
        retrieved_context = "\n".join(r.content for r in results)

        is_correct = judge(q.question, q.gold_answer, retrieved_context)
        tallies[q.category][1] += 1
        if is_correct:
            tallies[q.category][0] += 1
        logger.debug(
            "retrieval_quality: id=%s category=%s correct=%s question=%r gold=%r",
            q.id, q.category, is_correct, q.question, q.gold_answer,
        )

        if debug and not is_correct:
            _log_incorrect(q.id, q.category, q.question, q.gold_answer,
                           results, retrieved_context, flat_contexts.get(q.id, ""))

    category_scores = [
        CategoryScore(category=cat, correct=tallies[cat][0], total=tallies[cat][1])
        for cat in ABILITY_CATEGORIES
    ]

    deviation_notes: list[str] = []
    if embedding_provider_name not in _REAL_EMBEDDING_PROVIDERS:
        deviation_notes.append(
            f"Embedding provider '{embedding_provider_name}' is a lexical-overlap fallback, "
            "not a real semantic embedding model."
        )
    # judge_name may be "ollama", "ollama:deepseek-r1:8b", etc. — any ollama
    # variant is a real LLM judge; only keyword is the fallback heuristic.
    if judge_name not in _REAL_JUDGES and not judge_name.startswith("ollama:"):
        deviation_notes.append(
            f"Judge '{judge_name}' is a keyword-overlap heuristic, not an LLM judge."
        )
    is_longmemeval_comparable = not deviation_notes

    return RetrievalQualityResult(
        judge_name=judge_name,
        embedding_provider=embedding_provider_name,
        top_k=top_k,
        category_scores=category_scores,
        is_longmemeval_comparable=is_longmemeval_comparable,
        deviation_notes=deviation_notes,
    )


def run_baseline(
    judge,
    judge_name: str,
    n_per_category: int = 4,
    seed: int = 42,
) -> BaselineResult:
    """Execute the no-SDK flat-context baseline and return the result.

    Every session's turns are concatenated into a single string and handed
    directly to the judge as the ``retrieved_context`` — no ``remember()``,
    no ``search()``, no Db2 connection required. This is the "long-context
    LLM baseline" from LongMemEval (arXiv 2410.10813): the model sees all
    facts at once rather than retrieving them from a memory store.

    The delta between this score and :func:`run_retrieval_quality` answers:
    "does structured memory + vector retrieval beat stuffing everything into
    the prompt?" — a positive delta shows the SDK adds value over flat
    context; a negative delta means retrieval is losing relevant turns.

    Args:
        judge:          Callable matching ``LLMJudge``.
        judge_name:     Judge name — stamped into the report.
        n_per_category: Questions per ability category (must match the SDK
                        run being compared).
        seed:           RNG seed (must match the SDK run being compared).
    """
    # Baseline shares the same dataset (same seed / n_per_category) so the
    # comparison is over identical questions and facts.
    run_id = new_run_id()
    dataset = generate_dataset(run_id, n_per_category=n_per_category, seed=seed)

    tallies: dict[str, list[int]] = {cat: [0, 0] for cat in ABILITY_CATEGORIES}

    for q in dataset:
        # Flat context: every turn from every session, in session order.
        flat_context = "\n".join(
            turn for session in q.sessions for turn in session
        )

        is_correct = judge(q.question, q.gold_answer, flat_context)
        tallies[q.category][1] += 1
        if is_correct:
            tallies[q.category][0] += 1
        logger.debug(
            "baseline: id=%s category=%s correct=%s question=%r gold=%r",
            q.id, q.category, is_correct, q.question, q.gold_answer,
        )

    category_scores = [
        CategoryScore(category=cat, correct=tallies[cat][0], total=tallies[cat][1])
        for cat in ABILITY_CATEGORIES
    ]
    return BaselineResult(judge_name=judge_name, category_scores=category_scores)

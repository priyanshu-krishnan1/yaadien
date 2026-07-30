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

from agent_memory_sdk.models import WorkingMemory
from agent_memory_sdk.store import MemoryStore
from benchmarks.common.report import BaselineResult, CategoryScore, RetrievalQualityResult
from benchmarks.common.scope_gen import new_run_id
from benchmarks.retrieval_quality.dataset import ABILITY_CATEGORIES, generate_dataset

logger = logging.getLogger(__name__)

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
    """
    run_id = new_run_id()
    dataset = generate_dataset(run_id, n_per_category=n_per_category, seed=seed)

    tallies: dict[str, list[int]] = {cat: [0, 0] for cat in ABILITY_CATEGORIES}

    for q in dataset:
        for session in q.sessions:
            for turn in session:
                store.remember(
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
        results = store.working.search(
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

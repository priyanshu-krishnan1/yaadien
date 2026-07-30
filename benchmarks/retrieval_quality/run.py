"""
benchmarks/retrieval_quality/run.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Runs the synthetic LongMemEval-shaped dataset through
``MemoryStore.remember()`` / ``search()`` and scores answers with the
configured judge.

For each question: every session's turns are written in order via
``remember()`` (so later sessions really do land after earlier ones —
required for the knowledge_update and temporal_reasoning categories to be
meaningful), then the question is embedded and searched against that
question's own scope only (each question gets its own agent/user/thread, so
there is no cross-question interference within this suite — that is what
the isolation-under-load suite stresses separately, under concurrency).
"""

from __future__ import annotations

import logging

from agent_memory_sdk.models import WorkingMemory
from agent_memory_sdk.store import MemoryStore
from benchmarks.common.report import CategoryScore, RetrievalQualityResult
from benchmarks.common.scope_gen import new_run_id
from benchmarks.retrieval_quality.dataset import ABILITY_CATEGORIES, generate_dataset

logger = logging.getLogger(__name__)

#: Component names that constitute a real (non-fallback) semantic embedding
#: or real LLM-judge — used to decide whether a run's number may be labeled
#: LongMemEval-comparable in the report.
_REAL_EMBEDDING_PROVIDERS = {"sentence-transformers", "gemini"}
_REAL_JUDGES = {"gemini"}


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
    """Execute the retrieval-quality suite and return the aggregated result.

    Args:
        store:                   A live ``MemoryStore`` (real Db2 connection).
        embedding_provider:       Callable ``text -> list[float]``.
        embedding_provider_name:  One of "hashing", "sentence-transformers",
                                  "gemini" — used to stamp the report and
                                  decide LongMemEval-comparability.
        judge:                    Callable matching ``LLMJudge``.
        judge_name:               One of "keyword", "gemini" — same purpose.
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
    if judge_name not in _REAL_JUDGES:
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

"""
benchmarks/agent_quality/groundedness.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
AGQ-3 (EPIC-21): Faithfulness / groundedness judge.

Judges whether a generated answer's claims are actually supported by the
retrieved context, NOT just whether the final answer matches ground truth.

Key design insight (from BENCH-1)
----------------------------------
Correctness-only judges can be fooled: a model could answer correctly "by
chance" even when retrieval returned nothing useful.  Groundedness closes this
permanently by requiring every factual claim in the answer to be traceable to
the **retrieved** content that the memory store actually surfaced.

What this is NOT (distinction from TRU-3)
------------------------------------------
* TRU-3 checks whether write-time hooks resist contradicting / sycophantic
  input.
* AGQ-3 checks whether the READ-TIME generated answer stays faithful to what
  was actually retrieved.

They test different failure points and must never be merged.

Scoring scale (Microsoft Foundry 1-5)
--------------------------------------
* **1** — No claims supported by retrieved context (completely ungrounded /
  hallucinated).
* **2** — Few claims supported.
* **3** — Some claims supported.
* **4** — Most claims supported.
* **5** — All claims fully supported by retrieved context.

Note on "generated answer"
---------------------------
Since we do not have a full LLM responder, the gold answer from the dataset is
used as the "generated answer" (i.e. what the agent *should* have responded).
The retrieved_context is what ``store.search()`` actually returned.  This
design measures: given this retrieved context, is the gold answer
well-supported by it?  This is the correct design per AGQ-3.

No-memory control
-----------------
The same gold answer is also judged against an **empty** retrieved context.
The delta (with-memory score − no-memory score) is reported per question and
as an overall mean.

Output JSON shape (UNI-3 scorecard format)
------------------------------------------
::

    {
        "groundedness_mean": 3.8,
        "groundedness_per_category": {
            "single-session-user": 4.2,
            "knowledge-update": 3.1,
            ...
        },
        "no_memory_mean": 2.1,
        "delta_mean": 1.7,
        "judge_model": "llama3.1:8b",
        "judge_version": "1.0.0",
        "seed": 42
    }
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Protocol

from benchmarks.common.scope_gen import new_run_id as _new_run_id
from benchmarks.quality.longmemeval_adapter import (
    ABILITY_CATEGORIES as _ABILITY_CATEGORIES,
)
from benchmarks.quality.longmemeval_adapter import (
    iter_questions as _iter_questions,
)
from benchmarks.quality.longmemeval_adapter import (
    load_longmemeval,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version pin — bump when the prompt changes so old and new runs are
# distinguishable in the scorecard.
# ---------------------------------------------------------------------------

#: Bump whenever ``GROUNDEDNESS_JUDGE_PROMPT`` changes.
GROUNDEDNESS_JUDGE_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Judge prompt
# ---------------------------------------------------------------------------

#: Microsoft Foundry-style 1-5 groundedness judge prompt.
#: Variables: {question}, {retrieved_context}, {generated_answer}.
GROUNDEDNESS_JUDGE_PROMPT = """\
You are a faithfulness evaluation judge.  Your task is to determine whether the
claims made in a generated answer are supported by the retrieved context.

## Question
{question}

## Retrieved Context
{retrieved_context}

## Generated Answer
{generated_answer}

## Instructions
Read the Generated Answer carefully.  Identify every factual claim it makes.
For each claim, decide whether it is **directly supported** by the Retrieved
Context above.

Score the answer on a 1-5 scale:
  1 — No claims are supported by the retrieved context (completely ungrounded
      or hallucinated).
  2 — A few claims are supported, but the majority are not.
  3 — Some claims are supported and some are not (roughly balanced).
  4 — Most claims are supported; only minor details lack grounding.
  5 — All claims are fully supported by the retrieved context.

First, briefly explain which claims are supported and which are not.
Then output your final score on a **new line** in exactly this format:

Score: <integer from 1 to 5>
"""

# Regex that strips <think>…</think> blocks emitted by deepseek-r1 models
# before the actual score.  The block may span multiple lines.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Patterns used by _parse_score to extract a 1-5 integer from free-form LLM
# output.  Tried in order; first match wins.
_SCORE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"score\s*[:/]\s*([1-5])", re.IGNORECASE),
    re.compile(r"\b([1-5])\s*/\s*5\b"),
    re.compile(r"\brate\s+this\s+([1-5])\b", re.IGNORECASE),
    re.compile(r"\brating[:\s]+([1-5])\b", re.IGNORECASE),
    re.compile(r"\b([1-5])\b"),
)

# Default score when the model output is ambiguous.
_DEFAULT_SCORE = 3

# Human-readable label per --embedding-provider choice, stamped into the
# output JSON's embedding_provider_name field. "ollama" alone doesn't say
# which model was used, so it gets the specific model name; the others are
# unambiguous by name already.
_EMBEDDING_PROVIDER_LABELS = {
    "ollama": "ollama/nomic-embed-text",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class GroundednessResult:
    """Per-question groundedness result.

    Attributes:
        question_id:   Original LongMemEval question id.
        category:      Ability category (e.g. ``"single-session-user"``).
        score:         Groundedness score, 1–5 (with-memory judge call).
        no_memory_score: Groundedness score judged against empty context.
        delta:         ``score − no_memory_score``.
        raw_response:  Raw LLM output from the with-memory judge call.
        raw_no_memory_response: Raw LLM output from the no-memory judge call.
    """

    question_id: str
    category: str
    score: int
    no_memory_score: int
    delta: int
    raw_response: str
    raw_no_memory_response: str


@dataclass
class GroundednessRunResult:
    """Aggregated result of one AGQ-3 groundedness run.

    Attributes:
        groundedness_mean:          Mean 1-5 score across all questions
                                    (with retrieved context).
        groundedness_per_category:  Per-category mean 1-5 scores.
        no_memory_mean:             Mean 1-5 score judged against empty context.
        delta_mean:                 Mean of (with-memory − no-memory) per question.
        judge_model:                Ollama model tag used as judge.
        judge_version:              Prompt version pin.
        seed:                       Random seed stamped on this run.
        per_question:               Individual per-question results.
    """

    groundedness_mean: float
    groundedness_per_category: dict[str, float]
    no_memory_mean: float
    delta_mean: float
    judge_model: str
    judge_version: str
    seed: int
    per_question: list[GroundednessResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the UNI-3 scorecard JSON shape."""
        return {
            "groundedness_mean": self.groundedness_mean,
            "groundedness_per_category": self.groundedness_per_category,
            "no_memory_mean": self.no_memory_mean,
            "delta_mean": self.delta_mean,
            "judge_model": self.judge_model,
            "judge_version": self.judge_version,
            "seed": self.seed,
        }


# ---------------------------------------------------------------------------
# Protocol for type-hinting a store passed to run_groundedness
# ---------------------------------------------------------------------------


class _Searchable(Protocol):
    """Minimal structural type: anything with a .search() returning content."""

    def search(
        self,
        query: str,
        scope: Any,
        max_results: int = ...,
    ) -> list[Any]: ...


# ---------------------------------------------------------------------------
# OllamaGroundednessJudge
# ---------------------------------------------------------------------------


class OllamaGroundednessJudge:
    """Judge faithfulness of a generated answer against retrieved context.

    Uses ``GROUNDEDNESS_JUDGE_PROMPT`` with the Microsoft Foundry 1-5 scale.
    Strips ``<think>…</think>`` blocks before parsing (deepseek-r1
    compatibility).  Returns the integer score and the raw model output.

    Args:
        model: Ollama model tag to use as judge (default ``"llama3.1:8b"``).
        host:  Override the Ollama daemon URL (default ``http://localhost:11434``).
        seed:  Seed passed to Ollama for (best-effort) determinism.
    """

    def __init__(
        self,
        model: str = "llama3.1:8b",
        host: str | None = None,
        seed: int = 42,
    ) -> None:
        try:
            import ollama  # noqa: F401 — import-check only
        except ImportError as exc:
            raise ImportError(
                "OllamaGroundednessJudge requires the 'ollama' package: "
                "pip install ollama"
            ) from exc
        self.model = model
        self._host = host
        self.seed = seed

    def judge(
        self,
        question: str,
        retrieved_context: str,
        generated_answer: str,
    ) -> tuple[int, str]:
        """Judge whether *generated_answer* is grounded in *retrieved_context*.

        Args:
            question:          The original question text.
            retrieved_context: What the memory store actually returned for this
                               question (joined text of search results, or an
                               empty string for the no-memory control).
            generated_answer:  The answer whose faithfulness is being judged
                               (e.g. the gold answer from the dataset).

        Returns:
            A ``(score, raw_response)`` tuple where ``score`` is an integer in
            ``[1, 5]`` and ``raw_response`` is the full model output before any
            post-processing.
        """
        import ollama

        prompt = GROUNDEDNESS_JUDGE_PROMPT.format(
            question=question,
            retrieved_context=retrieved_context if retrieved_context else "(none)",
            generated_answer=generated_answer,
        )
        options: dict[str, Any] = {"seed": self.seed}
        if self._host:
            client = ollama.Client(host=self._host)
            response = client.generate(model=self.model, prompt=prompt, options=options)
        else:
            response = ollama.generate(model=self.model, prompt=prompt, options=options)

        raw: str = response["response"]
        score = self._parse_score(raw)
        return score, raw

    @staticmethod
    def _parse_score(raw: str) -> int:
        """Parse a 1-5 integer score from model output.

        Strips ``<think>…</think>`` blocks first (deepseek-r1 compatibility),
        then tries several patterns to extract an integer in ``[1, 5]``.
        Defaults to ``3`` when no match is found (ambiguous).

        Args:
            raw: Raw model response text.

        Returns:
            Integer score in ``[1, 5]``.
        """
        cleaned = _THINK_RE.sub("", raw).strip()
        for pattern in _SCORE_PATTERNS:
            match = pattern.search(cleaned)
            if match:
                try:
                    value = int(match.group(1))
                    if 1 <= value <= 5:
                        return value
                except (ValueError, IndexError):
                    continue
        return _DEFAULT_SCORE


# ---------------------------------------------------------------------------
# run_groundedness
# ---------------------------------------------------------------------------


def run_groundedness(
    store: Any,
    embedding_provider: Any,
    embedding_provider_name: str,
    judge: OllamaGroundednessJudge,
    judge_name: str,
    n_per_category: int = 10,
    seed: int = 42,
    top_k: int = 5,
    split: str = "longmemeval_s",
) -> GroundednessRunResult:
    """Run the AGQ-3 groundedness benchmark over the LongMemEval question set.

    For each question:
    1. Ingests the haystack sessions into the store using a unique per-question
       scope (no cross-question leakage).
    2. Retrieves context via ``store.search()``.
    3. Judges the gold answer against the retrieved context (with-memory score).
    4. Judges the gold answer against an empty context (no-memory control).
    5. Erases the per-question scope before the next question.

    Reports both absolute with-memory groundedness scores AND the delta vs the
    no-memory control, aggregated per category and as an overall mean.

    Args:
        store:                   Configured :class:`~agent_memory_sdk.store.MemoryStore`.
        embedding_provider:      Embedding provider used to embed questions for
                                 ``store.search()``.
        embedding_provider_name: Human-readable name of the embedding provider
                                 (e.g. ``"ollama/nomic-embed-text"``).
        judge:                   Configured :class:`OllamaGroundednessJudge`.
        judge_name:              Human-readable label for the judge model.
        n_per_category:          Maximum questions to sample from each of the
                                 six LongMemEval categories.
        seed:                    Random seed used when building run_id and
                                 stamped in the result.
        top_k:                   Number of search results to retrieve per
                                 question (passed to ``store.search()``).
        split:                   LongMemEval split to load (default
                                 ``"longmemeval_s"``).

    Returns:
        :class:`GroundednessRunResult` with per_category means, overall mean,
        no-memory mean, and delta mean.
    """
    import contextlib

    rows = load_longmemeval(split)
    run_id = _new_run_id()

    per_question_results: list[GroundednessResult] = []

    # Count questions processed per category so we can honour n_per_category.
    category_counts: dict[str, int] = {cat: 0 for cat in _ABILITY_CATEGORIES}

    for q in _iter_questions(rows, run_id):
        cat = q.category
        if cat not in category_counts:
            # Unknown category — include but do not cap.
            category_counts[cat] = 0
        if category_counts[cat] >= n_per_category:
            continue

        # --- Ingest haystack ---
        try:
            store.add_messages(q.haystack_messages, q.scope, extract_memories=False)
        except Exception:  # noqa: BLE001
            logger.warning(
                "AGQ-3: add_messages failed for question %s; skipping.", q.question_id
            )
            with contextlib.suppress(Exception):
                store.erase_all(q.scope)
            continue

        # --- Retrieve context ---
        try:
            results = store.search(
                query=q.question,
                scope=q.scope,
                max_results=top_k,
            )
            retrieved_context = "\n\n".join(
                r.content for r in results if hasattr(r, "content")
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "AGQ-3: search() failed for question %s; using empty context.",
                q.question_id,
            )
            retrieved_context = ""

        # --- With-memory judge call ---
        try:
            score, raw = judge.judge(
                question=q.question,
                retrieved_context=retrieved_context,
                generated_answer=q.gold_answer,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "AGQ-3: judge() failed for question %s; using default score.",
                q.question_id,
            )
            score, raw = _DEFAULT_SCORE, ""

        # --- No-memory control (empty context) ---
        try:
            no_score, raw_no = judge.judge(
                question=q.question,
                retrieved_context="",
                generated_answer=q.gold_answer,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "AGQ-3: no-memory judge() failed for question %s; using default.",
                q.question_id,
            )
            no_score, raw_no = _DEFAULT_SCORE, ""

        # --- Record result ---
        per_question_results.append(
            GroundednessResult(
                question_id=q.question_id,
                category=cat,
                score=score,
                no_memory_score=no_score,
                delta=score - no_score,
                raw_response=raw,
                raw_no_memory_response=raw_no,
            )
        )
        category_counts[cat] += 1

        # --- Erase per-question scope ---
        with contextlib.suppress(Exception):
            store.erase_all(q.scope)

    # --- Aggregate ---
    return _aggregate(
        per_question=per_question_results,
        judge_model=judge_name,
        seed=seed,
    )


def _aggregate(
    per_question: list[GroundednessResult],
    judge_model: str,
    seed: int,
) -> GroundednessRunResult:
    """Aggregate per-question results into a :class:`GroundednessRunResult`."""
    if not per_question:
        return GroundednessRunResult(
            groundedness_mean=0.0,
            groundedness_per_category={},
            no_memory_mean=0.0,
            delta_mean=0.0,
            judge_model=judge_model,
            judge_version=GROUNDEDNESS_JUDGE_VERSION,
            seed=seed,
            per_question=[],
        )

    all_scores = [r.score for r in per_question]
    all_no_scores = [r.no_memory_score for r in per_question]
    all_deltas = [r.delta for r in per_question]

    # Per-category means.
    cat_scores: dict[str, list[int]] = {}
    for r in per_question:
        cat_scores.setdefault(r.category, []).append(r.score)
    per_category_means = {
        cat: statistics.mean(scores) for cat, scores in cat_scores.items()
    }

    return GroundednessRunResult(
        groundedness_mean=statistics.mean(all_scores),
        groundedness_per_category=per_category_means,
        no_memory_mean=statistics.mean(all_no_scores),
        delta_mean=statistics.mean(all_deltas),
        judge_model=judge_model,
        judge_version=GROUNDEDNESS_JUDGE_VERSION,
        seed=seed,
        per_question=per_question,
    )

# ---------------------------------------------------------------------------
# CLI entrypoint — ``python -m benchmarks.agent_quality.groundedness``
# ---------------------------------------------------------------------------


def _cli_main(argv: list[str] | None = None) -> int:
    """Minimal CLI for running AGQ-3 groundedness benchmark.

    Connects to Db2 via environment variables (DB2_*) and runs the
    groundedness suite, writing JSON output to --output.
    """
    import argparse
    import json
    import sys
    from pathlib import Path

    p = argparse.ArgumentParser(
        description=(
            "AGQ-3 (EPIC-21): Faithfulness / groundedness judge over LongMemEval-S. "
            "Requires live Db2 (DB2_* env vars) and an Ollama server."
        )
    )
    p.add_argument("--judge-model", default="llama3.1:8b", metavar="MODEL")
    p.add_argument("--ollama-host", default=None, metavar="URL")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--n-per-category", type=int, default=4)
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument(
        "--embedding-provider",
        choices=["hashing", "sentence-transformers", "ollama"],
        default="ollama",
        metavar="PROVIDER",
        dest="embedding_provider",
        help=(
            "Embedding provider for store.search() queries. "
            "Default: ollama (requires a running Ollama daemon). "
            "Use 'hashing' for offline / CI runs without Ollama."
        ),
    )
    p.add_argument("--split", default="longmemeval_s")
    p.add_argument("--output", type=Path, metavar="FILE")
    args = p.parse_args(argv)

    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from benchmarks.common.embedding_providers import (
        build_embedding_provider,  # type: ignore[attr-defined]
    )
    judge = OllamaGroundednessJudge(
        model=args.judge_model,
        host=args.ollama_host,
        seed=args.seed,
    )
    embedding_provider = build_embedding_provider(args.embedding_provider)

    try:
        from agent_memory_sdk.db.connection import ConnectionPool
        from agent_memory_sdk.db.migrate import Migrator
        from agent_memory_sdk.store import MemoryStore

        pool = ConnectionPool()
        Migrator(pool).run()
        store = MemoryStore(
            pool=pool,
            embedding_provider=embedding_provider,
            enable_chunking=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Db2 connection failed: {exc}", file=sys.stderr)
        return 1

    result = run_groundedness(
        store=store,
        embedding_provider=embedding_provider,
        embedding_provider_name=_EMBEDDING_PROVIDER_LABELS.get(
            args.embedding_provider, args.embedding_provider
        ),
        judge=judge,
        judge_name=args.judge_model,
        n_per_category=args.n_per_category,
        seed=args.seed,
        top_k=args.top_k,
        split=args.split,
    )

    output_json = json.dumps(result.to_dict(), indent=2)
    print(output_json)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_json + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli_main())

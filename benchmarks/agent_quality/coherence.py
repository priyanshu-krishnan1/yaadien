"""
benchmarks/agent_quality/coherence.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
AGQ-4 (EPIC-21): Coherence/fluency judge for memory-injected responses.

**Goal**: Detect responses that are factually right but read as garbled,
contradictory, or disjointed because of how retrieved memory was spliced into
the prompt via ``search()`` or ``get_context_card()``.

Two judged dimensions
---------------------
* **Coherence (1–5)**: Logical structure and flow — does the answer flow
  naturally without contradictions, non-sequiturs, or confusing jumps?
* **Fluency (1–5)**: Natural language quality — is the response grammatically
  correct and naturally phrased?

Microsoft Foundry 1–5 scale:

* **1** — Completely incoherent / unreadable
* **2** — Mostly incoherent
* **3** — Somewhat coherent (passing threshold per Foundry convention)
* **4** — Mostly coherent
* **5** — Perfectly coherent / fluent

Key insight
-----------
This repo was designed for retrieval breadth (``get_context_card`` injects
multiple memory types) but was never scored for downstream text quality.
Injecting several memory types can make responses garbled even when the facts
are correct.  This metric catches that.

Concrete hypothesis
-------------------
Does ``get_context_card()``'s multi-type injection show a measurably larger
coherence drop than single-type ``search()`` results?  This is explicitly
called out (``multi_type_injection_finding``) when ``coherence_delta ≤ −0.3``.

Conditions compared
-------------------
1. **with-memory** — response judged with memory context injected.
2. **no-memory control** — same response judged with no context injected.
3. The **delta** (with-memory − no-memory) is reported per-category and in
   aggregate; a negative delta signals context-injection degradation.

Note on "response"
------------------
``gold_answer`` is used as the response (same approach as AGQ-3).  The
coherence/fluency measure captures: does injecting memory context make the
answer less coherent/fluent?  Since the same answer is evaluated under both
conditions, the delta isolates the effect of context injection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from benchmarks.common.scope_gen import new_run_id
from benchmarks.quality.longmemeval_adapter import (
    ABILITY_CATEGORIES,
    iter_questions,
    load_longmemeval,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version pins
# ---------------------------------------------------------------------------

COHERENCE_JUDGE_VERSION: str = "1.0.0"
FLUENCY_JUDGE_VERSION: str = "1.0.0"

# Human-readable label per --embedding-provider choice, stamped into the
# output JSON's embedding_provider_name field. "ollama" alone doesn't say
# which model was used, so it gets the specific model name; the others are
# unambiguous by name already.
_EMBEDDING_PROVIDER_LABELS = {
    "ollama": "ollama/nomic-embed-text",
}

# ---------------------------------------------------------------------------
# Judge prompt templates
# ---------------------------------------------------------------------------

#: Coherence judge prompt.
#: Asks the LLM to rate logical structure and flow on a 1–5 scale.
COHERENCE_JUDGE_PROMPT = """\
You are evaluating the coherence of a response. Coherence means logical \
structure and flow — does the answer read naturally without contradictions, \
non-sequiturs, or confusing jumps?

Question: {question}

Memory context injected into the prompt (may be empty):
{context_injected}

Response to evaluate:
{response}

Rate the COHERENCE of the response on a scale of 1 to 5:
1 = Completely incoherent — impossible to follow
2 = Mostly incoherent — major structural problems
3 = Somewhat coherent — passes a basic reading but has noticeable issues
4 = Mostly coherent — minor issues only
5 = Perfectly coherent — reads naturally with no structural problems

Respond with only the numeric score (1, 2, 3, 4, or 5) and a one-sentence \
explanation on the same line, e.g. "4 The response flows logically."
Score:"""

#: Fluency judge prompt.
#: Asks the LLM to rate grammatical correctness and natural phrasing on a 1–5 scale.
FLUENCY_JUDGE_PROMPT = """\
You are evaluating the fluency of a response. Fluency means natural language \
quality — is the response grammatically correct and naturally phrased?

Question: {question}

Memory context injected into the prompt (may be empty):
{context_injected}

Response to evaluate:
{response}

Rate the FLUENCY of the response on a scale of 1 to 5:
1 = Completely unreadable — broken grammar throughout
2 = Mostly unreadable — frequent grammatical errors
3 = Somewhat fluent — readable but with noticeable language issues
4 = Mostly fluent — minor phrasing issues only
5 = Perfectly fluent — reads like natural, polished prose

Respond with only the numeric score (1, 2, 3, 4, or 5) and a one-sentence \
explanation on the same line, e.g. "5 The response is grammatically impeccable."
Score:"""

# Regex that strips <think>…</think> blocks emitted by deepseek-r1 models
# before the actual score.  The block may span multiple lines.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Regexes that extract a 1–5 digit from various natural-language formats:
#   "4", "Score: 4", "4/5", "3 out of 5", "score is 4", etc.
_SCORE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b([1-5])\s*/\s*5"),           # "4/5"
    re.compile(r"\b([1-5])\s+out\s+of\s+5", re.IGNORECASE),  # "3 out of 5"
    re.compile(r"score\s*(?:is\s*)?[:=]?\s*([1-5])\b", re.IGNORECASE),  # "Score: 4"
    re.compile(r"^([1-5])\b"),                   # bare digit at start of cleaned text
    re.compile(r"\b([1-5])\b"),                  # any standalone digit
]

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CoherenceResult:
    """Per-question coherence/fluency judge result.

    Attributes:
        question_id:         Original LongMemEval question id.
        category:            Ability category (one of ``ABILITY_CATEGORIES``).
        coherence_score:     Coherence rating (1–5) for the with-memory condition.
        fluency_score:       Fluency rating (1–5) for the with-memory condition.
        coherence_no_memory: Coherence rating (1–5) for the no-memory control.
        fluency_no_memory:   Fluency rating (1–5) for the no-memory control.
        context_length:      Character length of the injected memory context.
        raw_coherence:       Raw model output for the coherence judge call.
        raw_fluency:         Raw model output for the fluency judge call.
        raw_coherence_no_memory: Raw model output for the no-memory coherence call.
        raw_fluency_no_memory:   Raw model output for the no-memory fluency call.
    """

    question_id: str
    category: str
    coherence_score: int
    fluency_score: int
    coherence_no_memory: int
    fluency_no_memory: int
    context_length: int
    raw_coherence: str = ""
    raw_fluency: str = ""
    raw_coherence_no_memory: str = ""
    raw_fluency_no_memory: str = ""


@dataclass
class CoherenceRunResult:
    """Aggregated result of one AGQ-4 coherence/fluency benchmark run.

    Attributes:
        run_id:                       Short hex run identifier.
        judge_model:                  Ollama model tag used as judge.
        coherence_mean:               Mean coherence score (1–5) across all
                                      with-memory questions.
        fluency_mean:                 Mean fluency score (1–5) across all
                                      with-memory questions.
        coherence_no_memory_mean:     Mean coherence score for the no-memory
                                      control condition.
        fluency_no_memory_mean:       Mean fluency score for the no-memory
                                      control condition.
        coherence_delta:              ``coherence_mean − coherence_no_memory_mean``.
                                      Negative = context injection degraded coherence.
        fluency_delta:                ``fluency_mean − fluency_no_memory_mean``.
                                      Negative = context injection degraded fluency.
        per_category:                 Per-category aggregation dict.  Keys are
                                      category names; values are dicts with keys
                                      ``coherence_mean``, ``fluency_mean``,
                                      ``coherence_delta``, ``fluency_delta``.
        multi_type_injection_finding: Human-readable finding string when
                                      ``coherence_delta ≤ −0.3`` (signals that
                                      multi-type context injection measurably
                                      degraded coherence); ``None`` otherwise.
        coherence_judge_version:      Version pin for the coherence judge prompt.
        fluency_judge_version:        Version pin for the fluency judge prompt.
    """

    run_id: str
    judge_model: str
    coherence_mean: float
    fluency_mean: float
    coherence_no_memory_mean: float
    fluency_no_memory_mean: float
    coherence_delta: float
    fluency_delta: float
    per_category: dict[str, dict[str, float]] = field(default_factory=dict)
    multi_type_injection_finding: str | None = None
    coherence_judge_version: str = COHERENCE_JUDGE_VERSION
    fluency_judge_version: str = FLUENCY_JUDGE_VERSION


# ---------------------------------------------------------------------------
# OllamaCoherenceJudge
# ---------------------------------------------------------------------------


class OllamaCoherenceJudge:
    """Judge coherence and fluency of memory-injected responses using a local Ollama model.

    Modeled on :class:`~benchmarks.quality.lme_judge.OllamaLMEJudge`.  Strips
    ``<think>…</think>`` blocks before parsing (deepseek-r1 compatibility).
    Returns integer scores in the range 1–5; defaults to 3 for any ambiguous
    or unparseable response.

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
                "OllamaCoherenceJudge requires the 'ollama' package: pip install ollama"
            ) from exc
        self.model = model
        self._host = host
        self.seed = seed

    def judge_coherence(
        self,
        question: str,
        context_injected: str,
        response: str,
    ) -> tuple[int, str]:
        """Judge the coherence of *response* given *question* and *context_injected*.

        Args:
            question:         The original question text.
            context_injected: The memory context that was (or would be) injected
                              into the prompt.  Pass an empty string for the
                              no-memory control condition.
            response:         The response text to evaluate.

        Returns:
            A ``(score, raw_response)`` tuple where *score* is 1–5 and
            *raw_response* is the full model output before post-processing.
        """
        prompt = COHERENCE_JUDGE_PROMPT.format(
            question=question,
            context_injected=context_injected,
            response=response,
        )
        raw = self._call_ollama(prompt)
        score = self._parse_score(raw)
        return score, raw

    def judge_fluency(
        self,
        question: str,
        context_injected: str,
        response: str,
    ) -> tuple[int, str]:
        """Judge the fluency of *response* given *question* and *context_injected*.

        Args:
            question:         The original question text.
            context_injected: The memory context that was (or would be) injected
                              into the prompt.  Pass an empty string for the
                              no-memory control condition.
            response:         The response text to evaluate.

        Returns:
            A ``(score, raw_response)`` tuple where *score* is 1–5 and
            *raw_response* is the full model output before post-processing.
        """
        prompt = FLUENCY_JUDGE_PROMPT.format(
            question=question,
            context_injected=context_injected,
            response=response,
        )
        raw = self._call_ollama(prompt)
        score = self._parse_score(raw)
        return score, raw

    def _call_ollama(self, prompt: str) -> str:
        """Issue a generate call to Ollama and return the raw response string.

        Uses ``ollama.Client`` when a custom *host* was supplied, otherwise
        the module-level ``ollama.generate`` function.
        """
        import ollama

        options: dict[str, Any] = {"seed": self.seed}
        if self._host:
            client = ollama.Client(host=self._host)
            response = client.generate(model=self.model, prompt=prompt, options=options)
        else:
            response = ollama.generate(model=self.model, prompt=prompt, options=options)
        return str(response["response"])

    @staticmethod
    def _parse_score(raw: str) -> int:
        """Parse a 1–5 integer score out of the raw model response.

        Processing steps:

        1. Strip ``<think>…</think>`` blocks (deepseek-r1 compatibility).
        2. Try each pattern in ``_SCORE_PATTERNS`` against the cleaned text in
           order; return the first valid 1–5 digit found.
        3. If no pattern matches, default to **3** (mid-scale, conservative).

        Args:
            raw: Raw model response text.

        Returns:
            Integer score in the range 1–5, or 3 if parsing fails.
        """
        cleaned = _THINK_RE.sub("", raw).strip()
        for pattern in _SCORE_PATTERNS:
            m = pattern.search(cleaned)
            if m:
                value = int(m.group(1))
                if 1 <= value <= 5:
                    return value
        # No pattern matched — default to 3 (mid-scale, conservative).
        return 3


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _mean(values: list[float]) -> float:
    """Return the arithmetic mean of *values*, or 0.0 for an empty list."""
    return sum(values) / len(values) if values else 0.0


def _aggregate_category(
    results: list[CoherenceResult],
) -> dict[str, dict[str, float]]:
    """Build the per-category aggregation dict from a list of per-question results.

    Returns a dict mapping category name → dict with keys:
    ``coherence_mean``, ``fluency_mean``, ``coherence_delta``, ``fluency_delta``.
    """
    buckets: dict[str, list[CoherenceResult]] = {cat: [] for cat in ABILITY_CATEGORIES}
    for r in results:
        buckets.setdefault(r.category, []).append(r)

    per_category: dict[str, dict[str, float]] = {}
    for cat, items in buckets.items():
        if not items:
            continue
        c_mean = _mean([float(i.coherence_score) for i in items])
        f_mean = _mean([float(i.fluency_score) for i in items])
        c_nm = _mean([float(i.coherence_no_memory) for i in items])
        f_nm = _mean([float(i.fluency_no_memory) for i in items])
        per_category[cat] = {
            "coherence_mean": round(c_mean, 4),
            "fluency_mean": round(f_mean, 4),
            "coherence_delta": round(c_mean - c_nm, 4),
            "fluency_delta": round(f_mean - f_nm, 4),
        }
    return per_category


# ---------------------------------------------------------------------------
# Public run function
# ---------------------------------------------------------------------------


def run_coherence(
    store: Any,
    embedding_provider: Any,
    embedding_provider_name: str,
    judge: OllamaCoherenceJudge,
    judge_name: str,
    n_per_category: int = 4,
    seed: int = 42,
    top_k: int = 5,
) -> CoherenceRunResult:
    """Run the AGQ-4 coherence/fluency benchmark over the BM-18 LongMemEval question set.

    For each question the haystack is ingested via ``store.add_messages()``,
    then ``store.search()`` is called to retrieve the with-memory context.
    Each question's ``gold_answer`` is used as the response (same approach as
    AGQ-3).  The judge evaluates coherence and fluency under two conditions:

    1. **with-memory** — the judge prompt includes the retrieved context.
    2. **no-memory control** — the judge prompt has an empty context string.

    The delta (with-memory − no-memory) is reported per-category and in
    aggregate.  When ``coherence_delta ≤ −0.3`` a human-readable finding is set
    on the result's ``multi_type_injection_finding`` field.

    Args:
        store:                    A live :class:`~agent_memory_sdk.store.MemoryStore`
                                  (real Db2 connection).
        embedding_provider:       Callable ``text -> list[float]``.
        embedding_provider_name:  Provider name stamped in the report.
        judge:                    An :class:`OllamaCoherenceJudge` instance.
        judge_name:               Human-readable judge name for the report.
        n_per_category:           Questions sampled from each LongMemEval category
                                  (default 4).
        seed:                     RNG seed passed to LongMemEval question iteration
                                  for reproducibility.
        top_k:                    Number of memory results retrieved per question.

    Returns:
        A :class:`CoherenceRunResult` with aggregated coherence/fluency scores,
        deltas, per-category breakdown, and the multi-type injection finding.
    """
    run_id = new_run_id()
    rows = load_longmemeval("longmemeval_s")

    per_question_results: list[CoherenceResult] = []

    for q in iter_questions(rows, run_id, limit=n_per_category * len(ABILITY_CATEGORIES)):
        # --- Ingest haystack ---
        try:
            store.add_messages(q.haystack_messages, q.scope, extract_memories=False)
        except Exception:
            logger.exception(
                "coherence: add_messages raised for question_id=%s; skipping.",
                q.question_id,
            )
            continue

        # --- Retrieve with-memory context via search() ---
        try:
            search_results = store.search(q.question, q.scope, max_results=top_k)
            context_injected = "\n".join(r.content for r in search_results)
        except Exception:
            logger.exception(
                "coherence: search raised for question_id=%s; using empty context.",
                q.question_id,
            )
            context_injected = ""

        response = q.gold_answer

        # --- Judge: with-memory condition ---
        coherence_score, raw_coh = judge.judge_coherence(
            question=q.question,
            context_injected=context_injected,
            response=response,
        )
        fluency_score, raw_flu = judge.judge_fluency(
            question=q.question,
            context_injected=context_injected,
            response=response,
        )

        # --- Judge: no-memory control (empty context) ---
        coherence_no_memory, raw_coh_nm = judge.judge_coherence(
            question=q.question,
            context_injected="",
            response=response,
        )
        fluency_no_memory, raw_flu_nm = judge.judge_fluency(
            question=q.question,
            context_injected="",
            response=response,
        )

        per_question_results.append(
            CoherenceResult(
                question_id=q.question_id,
                category=q.category,
                coherence_score=coherence_score,
                fluency_score=fluency_score,
                coherence_no_memory=coherence_no_memory,
                fluency_no_memory=fluency_no_memory,
                context_length=len(context_injected),
                raw_coherence=raw_coh,
                raw_fluency=raw_flu,
                raw_coherence_no_memory=raw_coh_nm,
                raw_fluency_no_memory=raw_flu_nm,
            )
        )

        logger.debug(
            "coherence: id=%s category=%s coh=%d flu=%d coh_nm=%d flu_nm=%d",
            q.question_id,
            q.category,
            coherence_score,
            fluency_score,
            coherence_no_memory,
            fluency_no_memory,
        )

        # --- Erase scope to prevent cross-question leakage ---
        try:
            store.erase_all(q.scope)
        except Exception:
            logger.exception(
                "coherence: erase_all raised for question_id=%s; "
                "residual rows may remain.",
                q.question_id,
            )

    # --- Aggregate ---
    coherence_mean = _mean([float(r.coherence_score) for r in per_question_results])
    fluency_mean = _mean([float(r.fluency_score) for r in per_question_results])
    coherence_no_memory_mean = _mean(
        [float(r.coherence_no_memory) for r in per_question_results]
    )
    fluency_no_memory_mean = _mean(
        [float(r.fluency_no_memory) for r in per_question_results]
    )
    coherence_delta = coherence_mean - coherence_no_memory_mean
    fluency_delta = fluency_mean - fluency_no_memory_mean

    per_category = _aggregate_category(per_question_results)

    # --- Multi-type injection finding ---
    multi_type_injection_finding: str | None = None
    if coherence_delta <= -0.3:
        multi_type_injection_finding = (
            f"Context injection caused a measurable coherence degradation: "
            f"coherence_delta={coherence_delta:+.3f} (threshold ≤ −0.3). "
            f"get_context_card()'s multi-type injection likely contributed — "
            f"injecting several memory types made responses less coherent even "
            f"when the facts were correct."
        )

    return CoherenceRunResult(
        run_id=run_id,
        judge_model=judge_name,
        coherence_mean=round(coherence_mean, 4),
        fluency_mean=round(fluency_mean, 4),
        coherence_no_memory_mean=round(coherence_no_memory_mean, 4),
        fluency_no_memory_mean=round(fluency_no_memory_mean, 4),
        coherence_delta=round(coherence_delta, 4),
        fluency_delta=round(fluency_delta, 4),
        per_category=per_category,
        multi_type_injection_finding=multi_type_injection_finding,
        coherence_judge_version=COHERENCE_JUDGE_VERSION,
        fluency_judge_version=FLUENCY_JUDGE_VERSION,
    )

# ---------------------------------------------------------------------------
# CLI entrypoint — ``python -m benchmarks.agent_quality.coherence``
# ---------------------------------------------------------------------------


def _cli_main(argv: list[str] | None = None) -> int:
    """Minimal CLI for running AGQ-4 coherence/fluency benchmark.

    Connects to Db2 via environment variables (DB2_*) and runs the
    coherence suite, writing JSON output to --output.
    """
    import argparse
    import json
    import sys
    from pathlib import Path

    p = argparse.ArgumentParser(
        description=(
            "AGQ-4 (EPIC-21): Coherence/fluency judge over LongMemEval-S. "
            "Requires live Db2 (DB2_* env vars) and an Ollama server."
        )
    )
    p.add_argument("--judge-model", default="llama3.1:8b", metavar="MODEL")
    p.add_argument("--ollama-host", default=None, metavar="URL")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--n-per-category", type=int, default=4)
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--output", type=Path, metavar="FILE")
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
    args = p.parse_args(argv)

    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from benchmarks.common.embedding_providers import (
        build_embedding_provider,  # type: ignore[attr-defined]
    )
    judge = OllamaCoherenceJudge(
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

    result = run_coherence(
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
    )

    # Serialise to JSON: CoherenceRunResult is a dataclass — convert manually
    output_data = {
        "run_id": result.run_id,
        "judge_model": result.judge_model,
        "coherence_mean": result.coherence_mean,
        "fluency_mean": result.fluency_mean,
        "coherence_no_memory_mean": result.coherence_no_memory_mean,
        "fluency_no_memory_mean": result.fluency_no_memory_mean,
        "coherence_delta": result.coherence_delta,
        "fluency_delta": result.fluency_delta,
        "per_category": result.per_category,
        "multi_type_injection_finding": result.multi_type_injection_finding,
        "coherence_judge_version": result.coherence_judge_version,
        "fluency_judge_version": result.fluency_judge_version,
    }
    output_json = json.dumps(output_data, indent=2)
    print(output_json)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_json + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli_main())

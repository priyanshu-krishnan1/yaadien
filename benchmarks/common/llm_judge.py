"""
benchmarks/common/llm_judge.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Judges that score a retrieved answer against the expected (gold) answer for
the retrieval-quality suite.

LongMemEval (arXiv 2410.10813) scores answer correctness with an LLM judge
(the paper uses GPT-4o-as-judge), not exact string match — free-form answers
phrased differently from the gold answer can still be correct. This module
provides:

* :class:`LLMJudge` — the protocol a real judge must satisfy.
* :class:`KeywordMatchJudge` — a dependency-free, no-network fallback that
  approximates correctness by keyword/token overlap. This is a **documented
  methodology deviation**: it is not the LongMemEval judging methodology and
  any report produced with it must say so explicitly rather than being
  presented as a LongMemEval-comparable number (see report.py, which stamps
  every report with the judge that was actually used).
* :class:`GeminiJudge` — a real LLM-judge using Google's free-tier Gemini
  API, prompted the way LongMemEval's judge is used (correct/incorrect
  verdict against the gold answer). This is the mode that produces a number
  honestly comparable to vendor-reported figures.
"""

from __future__ import annotations

import re
from typing import Protocol

_TOKEN_RE = re.compile(r"[a-z0-9]+")

#: Stopwords excluded from the keyword-overlap heuristic so overlap isn't
#: dominated by function words that carry no factual content.
_STOPWORDS = frozenset([
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "and", "or", "not", "no",
    "do", "does", "did", "have", "has", "had", "this", "that", "these",
    "those", "it", "its", "as", "by", "from", "about", "into", "over",
    "under", "again", "further", "then", "once", "here", "there", "when",
    "where", "why", "how", "all", "any", "both", "each", "few", "more",
    "most", "other", "some", "such", "only", "own", "same", "so", "than",
    "too", "very", "can", "will", "just", "should", "now",
])


def _content_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS}


class LLMJudge(Protocol):
    """Protocol for a judge that scores a retrieved answer against a gold answer.

    Shape::

        (question: str, gold_answer: str, retrieved_context: str) -> bool

    Returns True if the retrieved context supports/contains the correct
    answer to the question, False otherwise (including LongMemEval's
    abstention category, where the correct behavior is to NOT assert an
    answer that isn't supported by the retrieved context).
    """

    def __call__(self, question: str, gold_answer: str, retrieved_context: str) -> bool:
        ...

    #: Human-readable identifier stamped into BENCHMARKS.md so the report is
    #: explicit about which judge produced the score.
    name: str


class KeywordMatchJudge:
    """Dependency-free fallback judge: keyword/token-overlap heuristic.

    Scores True if a configurable fraction of the gold answer's
    content-bearing tokens (stopwords excluded) appear in the retrieved
    context. For the abstention category (``gold_answer == ""``), scores
    True only if the retrieved context is empty/near-empty — approximating
    "the system correctly found nothing to assert."

    This is a coarse proxy for LLM-judged correctness and will both
    over-count (context happens to share words without actually answering
    the question) and under-count (a correct paraphrase using different
    words). It exists so the harness can run with zero setup; use
    :class:`GeminiJudge` (or your own real LLM judge) for a number that
    means anything next to vendor-reported LongMemEval figures.

    Args:
        overlap_threshold: Fraction (0-1) of gold-answer content tokens that
            must appear in the retrieved context for a True verdict.
            Default 0.6.
    """

    name = "keyword-overlap-fallback (NOT an LLM judge — see BENCHMARKS.md caveat)"

    def __init__(self, overlap_threshold: float = 0.6) -> None:
        self._threshold = overlap_threshold

    def __call__(self, question: str, gold_answer: str, retrieved_context: str) -> bool:
        context_tokens = _content_tokens(retrieved_context)

        if not gold_answer.strip():
            # Abstention case: correct behavior is retrieving (near-)nothing
            # relevant. Approximate via "no gold-irrelevant heuristic" is not
            # possible without a gold answer to compare against, so instead
            # treat an empty/very sparse retrieved context as correct
            # abstention, matching the LongMemEval abstention intent.
            return len(context_tokens) == 0

        gold_tokens = _content_tokens(gold_answer)
        if not gold_tokens:
            return True
        matched = len(gold_tokens & context_tokens)
        return (matched / len(gold_tokens)) >= self._threshold


class GeminiJudge:
    """Real LLM-judge using Google's free-tier Gemini API.

    Prompts the model to output exactly ``CORRECT`` or ``INCORRECT`` given
    the question, gold answer, and retrieved context — the same
    judge-verdict shape LongMemEval's GPT-4o judge produces. Requires
    ``pip install google-generativeai`` and a ``GEMINI_API_KEY`` environment
    variable.

    Args:
        model:   Gemini model id. Defaults to ``gemini-1.5-flash`` (fast,
                 generous free-tier quota — appropriate for a judge role).
        api_key: Overrides the ``GEMINI_API_KEY`` env var if supplied.
    """

    def __init__(self, model: str = "gemini-1.5-flash", api_key: str | None = None) -> None:
        import os

        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ImportError(
                "GeminiJudge requires 'google-generativeai': "
                "pip install google-generativeai"
            ) from exc

        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError(
                "GeminiJudge requires a GEMINI_API_KEY environment variable "
                "(or an explicit api_key argument)."
            )
        genai.configure(api_key=key)
        self._client = genai.GenerativeModel(model)
        self.name = f"llm-judge:{model} (LongMemEval-style correct/incorrect verdict)"

    def __call__(self, question: str, gold_answer: str, retrieved_context: str) -> bool:
        prompt = (
            "You are grading whether a memory-retrieval system's retrieved "
            "context supports the correct answer to a question. "
            "If the gold answer is empty, the question is an abstention case: "
            "the retrieved context should NOT contain a confident answer, "
            "and the correct verdict is CORRECT only if no answer is asserted.\n\n"
            f"Question: {question}\n"
            f"Gold answer: {gold_answer or '(none — this is an abstention case)'}\n"
            f"Retrieved context:\n{retrieved_context}\n\n"
            "Respond with exactly one word: CORRECT or INCORRECT."
        )
        response = self._client.generate_content(prompt)
        verdict = (response.text or "").strip().upper()
        return verdict.startswith("CORRECT")


def build_judge(name: str) -> LLMJudge:
    """Factory used by the CLI entry point to select a judge by name."""
    if name == "keyword":
        return KeywordMatchJudge()
    if name == "gemini":
        return GeminiJudge()
    raise ValueError(f"Unknown judge {name!r}. Expected one of: keyword, gemini.")

"""
benchmarks/quality/lme_judge.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-18 (EPIC-16): LLM-judged end-to-end accuracy — offline tier.

Implements the LongMemEval official judge prompt with a local Ollama model to
run the full 500-question ``longmemeval_s`` split end to end, reporting
per-category accuracy.  Every result is stamped with the judge model, seed,
embedding provider name, and top-k so runs are fully reproducible and
comparable.

Key design choices
------------------
* **Official LongMemEval judge prompt** — ``LME_JUDGE_PROMPT`` is taken from
  the paper's supplementary materials, not the discarded ``common/llm_judge.py``
  heuristic.  This directly implements Finding 4 from the strategy doc.
* **Ollama, not OpenAI** — zero API cost, fully offline.  The deviation is
  explicitly enumerated in every result's ``deviation_notes`` so callers never
  accidentally claim apples-to-apples parity with the published GPT-4o figures.
* **deepseek-r1 compatibility** — ``<think>…</think>`` blocks are stripped
  before parsing the verdict, matching the fix already applied to the old judge
  in Run C.
* **Judge variance** — a 30-question fixed subset (first 5 per category) is
  judged 3× with different seeds; the spread (max − min) is the concrete,
  measured answer to BENCH-1's anecdotal finding about non-determinism.

This tier is NEVER a merge gate — mark nightly/scale tests with
``benchmark_nightly`` or ``benchmark_scale``, not ``benchmark_pr``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Official LongMemEval judge prompt (from the paper's supplementary materials)
# ---------------------------------------------------------------------------

#: LongMemEval's official judge prompt template.
#: Asks the LLM to respond CORRECT or INCORRECT given a question, gold answer,
#: and the retrieved context (the system-under-test's answer).
LME_JUDGE_PROMPT = """Given the following question and ground truth answer, \
determine if the provided answer is correct.

Question: {question}
Ground truth: {gold_answer}
Provided answer: {retrieved_context}

Is the provided answer CORRECT or INCORRECT? Respond with only 'CORRECT' or 'INCORRECT'."""

# Regex that strips <think>…</think> blocks emitted by deepseek-r1 models
# before the actual verdict. The block may span multiple lines.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# The six LongMemEval ability categories (matches ABILITY_CATEGORIES in the adapter).
LME_CATEGORIES: tuple[str, ...] = (
    "single-session-user",
    "single-session-assistant",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
    "abstention",
)

# Number of questions sampled from each category for the variance subset.
_VARIANCE_SUBSET_PER_CATEGORY: int = 5

# Number of repeated judge runs for variance measurement.
_VARIANCE_RUNS: int = 3


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class JudgeVerdict:
    """Single judge decision for one question.

    Attributes:
        question_id:   Original LongMemEval question id.
        category:      Ability category (one of ``LME_CATEGORIES``).
        is_correct:    ``True`` if the judge returned CORRECT.
        raw_response:  The raw text returned by the Ollama model (before
                       ``<think>`` stripping or verdict parsing).
    """

    question_id: str
    category: str
    is_correct: bool
    raw_response: str


@dataclass
class LMEJudgeResult:
    """Aggregated result of one BM-18 judge run.

    Attributes:
        run_id:                    Short hex run identifier.
        split:                     LongMemEval split name (e.g. ``longmemeval_s``).
        judge_model:               Ollama model tag used as judge.
        seed:                      Random seed stamped on this run.
        embedding_provider_name:   Human-readable name of the embedding provider
                                   used for retrieval (e.g. ``"ollama/nomic-embed-text"``).
        top_k:                     Number of top-k results retrieved per question.
        per_category:              Dict mapping category name → list of boolean
                                   correctness verdicts, one per question.
        judge_variance_subset_ids: Question ids in the 30-question variance subset.
        judge_variance_runs:       Per-run accuracy on the variance subset (3 floats).
        deviation_notes:           Explicit list of methodology deviations from the
                                   published LongMemEval benchmark.
    """

    run_id: str
    split: str
    judge_model: str
    seed: int
    embedding_provider_name: str
    top_k: int
    per_category: dict[str, list[bool]] = field(default_factory=dict)
    judge_variance_subset_ids: list[str] = field(default_factory=list)
    judge_variance_runs: list[float] = field(default_factory=list)
    deviation_notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# OllamaLMEJudge
# ---------------------------------------------------------------------------


class OllamaLMEJudge:
    """Judge correctness of a retrieved answer using a local Ollama model.

    Uses ``LME_JUDGE_PROMPT`` — the official LongMemEval judge prompt from the
    paper's supplementary materials.  Strips ``<think>…</think>`` blocks before
    parsing (deepseek-r1 compatibility).  Returns ``True`` for CORRECT, ``False``
    for INCORRECT or any ambiguous response.

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
                "OllamaLMEJudge requires the 'ollama' package: pip install ollama"
            ) from exc
        self.model = model
        self._host = host
        self.seed = seed

    def judge(
        self,
        question: str,
        gold_answer: str,
        retrieved_context: str,
    ) -> tuple[bool, str]:
        """Judge whether *retrieved_context* correctly answers *question*.

        Args:
            question:          The original question text.
            gold_answer:       The dataset ground-truth answer.
            retrieved_context: The system-under-test's answer / retrieved text.

        Returns:
            A ``(is_correct, raw_response)`` tuple where ``is_correct`` is
            ``True`` iff the judge returned CORRECT and ``raw_response`` is the
            full model output before any post-processing.
        """
        import ollama

        prompt = LME_JUDGE_PROMPT.format(
            question=question,
            gold_answer=gold_answer,
            retrieved_context=retrieved_context,
        )
        options: dict[str, Any] = {"seed": self.seed}
        if self._host:
            client = ollama.Client(host=self._host)
            response = client.generate(model=self.model, prompt=prompt, options=options)
        else:
            response = ollama.generate(model=self.model, prompt=prompt, options=options)

        raw: str = response["response"]
        is_correct = self._parse_verdict(raw)
        return is_correct, raw

    @staticmethod
    def _parse_verdict(raw: str) -> bool:
        """Parse the model output into a boolean correctness verdict.

        Strips ``<think>…</think>`` blocks first (deepseek-r1 compatibility),
        then searches for the first occurrence of CORRECT or INCORRECT.  If
        neither is found the verdict defaults to ``False`` (conservative).

        Args:
            raw: Raw model response text.

        Returns:
            ``True`` if CORRECT appears before INCORRECT (or only CORRECT),
            ``False`` otherwise (INCORRECT, ambiguous, or empty).
        """
        cleaned = _THINK_RE.sub("", raw).strip()
        upper = cleaned.upper()
        correct_pos = upper.find("CORRECT")
        incorrect_pos = upper.find("INCORRECT")

        if correct_pos == -1 and incorrect_pos == -1:
            # Neither keyword found — default to False (conservative).
            return False
        if incorrect_pos == -1:
            # Only CORRECT found.
            return True
        if correct_pos == -1:
            # Only INCORRECT found.
            return False
        # Both found — INCORRECT contains "CORRECT" as a substring, so the
        # only unambiguous CORRECT signal is when correct_pos < incorrect_pos
        # AND the match at correct_pos is not part of "INCORRECT".
        # The safest check: if the first hit is "INCORRECT" (starts at
        # incorrect_pos) rather than a standalone "CORRECT", return False.
        return correct_pos < incorrect_pos and not upper[correct_pos:].startswith("INCORRECT")


# ---------------------------------------------------------------------------
# Deviation note helpers
# ---------------------------------------------------------------------------

_PUBLISHED_JUDGE = "GPT-4o"


def build_deviation_notes(
    judge_model: str,
    embedding_provider_name: str,
    seed: int,
) -> list[str]:
    """Build the standard list of methodology deviation notes for a run.

    Always includes the dataset note and the seed note.  Adds model and
    embedding-provider deviation notes when they differ from the published
    LongMemEval methodology.

    Args:
        judge_model:             Ollama model tag used as judge.
        embedding_provider_name: Human-readable embedding provider name.
        seed:                    Seed value for this run.

    Returns:
        List of deviation note strings for inclusion in ``LMEJudgeResult``.
    """
    notes: list[str] = []
    if judge_model != _PUBLISHED_JUDGE:
        notes.append(
            f"Judge model is {judge_model!r}, not {_PUBLISHED_JUDGE!r} "
            f"(published benchmark uses {_PUBLISHED_JUDGE})"
        )
    notes.append(
        "Dataset is the real LongMemEval longmemeval_s (500 questions, Apache-2.0) "
        "— methodology comparable in kind to published figures but NOT apples-to-apples"
    )
    if "nomic-embed-text" not in embedding_provider_name.lower():
        notes.append(
            f"Embedding provider is {embedding_provider_name!r}; "
            "published benchmark uses a different (undisclosed) embedding provider"
        )
    else:
        notes.append(
            f"Embedding provider is {embedding_provider_name!r} (Ollama local); "
            "published benchmark uses a different (undisclosed) embedding provider"
        )
    notes.append(f"Seed stamped on every run: {seed} (for reproducibility)")
    return notes


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------


def format_benchmark_run_markdown(result: LMEJudgeResult, date: str) -> str:
    """Format a ``LMEJudgeResult`` as a BENCHMARKS.md-style run section.

    Matches the existing Run A/B/C/D formatting in ``project-management/BENCHMARKS.md``
    (field table, per-category accuracy table, overall accuracy, judge variance section,
    and explicit deviation notes).

    DOES NOT claim apples-to-apples comparison to published LongMemEval figures —
    ``deviation_notes`` are listed verbatim under a dedicated sub-section.

    Args:
        result: The judge run result to format.
        date:   ISO date string for the run header (e.g. ``"2026-09-01"``).

    Returns:
        Markdown string for the new run section, ready to append to BENCHMARKS.md.
    """
    # --- per-category rows ---
    category_rows: list[str] = []
    total_correct = 0
    total_questions = 0
    for cat in LME_CATEGORIES:
        verdicts = result.per_category.get(cat, [])
        n = len(verdicts)
        c = sum(verdicts)
        total_correct += c
        total_questions += n
        acc = f"{100.0 * c / n:.1f}%" if n > 0 else "n/a"
        category_rows.append(f"| {cat} | {c} | {n} | {acc} |")

    overall_acc = (
        f"{100.0 * total_correct / total_questions:.1f}%"
        if total_questions > 0
        else "n/a"
    )

    # --- judge variance ---
    if result.judge_variance_runs:
        var_min = min(result.judge_variance_runs)
        var_max = max(result.judge_variance_runs)
        spread = var_max - var_min
        variance_runs_str = ", ".join(f"{v:.1%}" for v in result.judge_variance_runs)
        n_runs = len(result.judge_variance_runs)
        variance_section = (
            f"\n#### Judge variance (BENCH-1 quantification)\n\n"
            f"Fixed 30-question subset (first 5 per category) judged {n_runs}× "
            f"with seeds {result.seed}, {result.seed + 1}, {result.seed + 2}.\n\n"
            f"| Run | Subset accuracy |\n"
            f"|---|---|\n"
        )
        for i, acc in enumerate(result.judge_variance_runs):
            variance_section += f"| Run {i + 1} (seed={result.seed + i}) | {acc:.1%} |\n"
        variance_section += (
            f"\n**Spread (max − min): {spread:.1%}** across {len(result.judge_variance_runs)} runs "
            f"({variance_runs_str}).\n\n"
            f"This is the measured judge variance on this judge model and subset — "
            f"not an assumption. See BENCH-1 in this document for the anecdotal "
            f"background that motivated this measurement.\n"
        )
    else:
        variance_section = "\n#### Judge variance\n\n*Not measured in this run.*\n"

    # --- deviation notes ---
    deviation_lines = "\n".join(f"{i + 1}. {note}" for i, note in enumerate(result.deviation_notes))

    lines = [
        f"### Run E — BM-18 LLM-judged end-to-end accuracy (offline tier): "
        f"`{result.judge_model}`, split: `{result.split}`",
        "",
        "**Tier: offline — reported, never gated. Never a merge gate.**",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| **Date** | {date} |",
        f"| **Run id** | `{result.run_id}` |",
        f"| **Split** | `{result.split}` (500 questions, Apache-2.0) |",
        f"| **Judge** | `ollama:{result.judge_model}` |",
        f"| **Seed** | {result.seed} |",
        f"| **Embedding provider** | `{result.embedding_provider_name}` |",
        f"| **top_k** | {result.top_k} |",
        "",
        "| Category | Correct | Total | Accuracy |",
        "|---|---|---|---|",
        *category_rows,
        f"| **Overall** | **{total_correct}** | **{total_questions}** | **{overall_acc}** |",
        "",
        "> **Methodology deviations — do not treat as apples-to-apples vs. published",
        "> LongMemEval figures (which use GPT-4o judge, undisclosed embedding provider,",
        "> and the full 500-question dataset with the paper's retrieval setup).**",
        "",
        "#### Methodology deviations from published LongMemEval",
        "",
        deviation_lines,
        "",
        variance_section,
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# BENCHMARKS.md append helper
# ---------------------------------------------------------------------------


def append_to_benchmarks_md(result: LMEJudgeResult, benchmarks_path: Path) -> None:
    """Append a new dated run section to BENCHMARKS.md without modifying existing content.

    Reads the current file, appends the new run section at the end, and writes
    the result back.  Existing runs (A–D and any prior BM-18 runs) are
    preserved exactly.

    Args:
        result:           The judge run result to append.
        benchmarks_path:  Absolute or relative path to ``BENCHMARKS.md``.

    Raises:
        FileNotFoundError: If *benchmarks_path* does not exist.
    """
    import datetime

    date = datetime.date.today().isoformat()
    new_section = format_benchmark_run_markdown(result, date)

    existing = benchmarks_path.read_text(encoding="utf-8")
    updated = existing.rstrip("\n") + "\n\n" + new_section
    benchmarks_path.write_text(updated, encoding="utf-8")

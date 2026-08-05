"""
benchmarks/quality/test_lme_judge.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-18 unit tests for the LongMemEval official judge implementation.

All tests are ``benchmark_micro`` — no Db2, no real Ollama required (mocked).

Coverage
--------
- Verdict parsing: CORRECT → True, INCORRECT → False, ambiguous → False
- deepseek-r1 <think>…</think> stripping
- Markdown formatting: all 6 categories present, deviation notes present,
  judge variance section present
- BENCHMARKS.md append: sentinel content preserved, new section added after it
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchmarks.quality.lme_judge import (
    LME_CATEGORIES,
    LMEJudgeResult,
    OllamaLMEJudge,
    append_to_benchmarks_md,
    build_deviation_notes,
    format_benchmark_run_markdown,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    per_category: dict[str, list[bool]] | None = None,
    judge_model: str = "llama3.1:8b",
    seed: int = 42,
    variance_runs: list[float] | None = None,
    deviation_notes: list[str] | None = None,
) -> LMEJudgeResult:
    """Build a minimal LMEJudgeResult for formatting tests."""
    if per_category is None:
        per_category = {cat: [True, False, True, True, False] for cat in LME_CATEGORIES}
    return LMEJudgeResult(
        run_id=uuid.uuid4().hex[:12],
        split="longmemeval_s",
        judge_model=judge_model,
        seed=seed,
        embedding_provider_name="ollama/nomic-embed-text",
        top_k=5,
        per_category=per_category,
        judge_variance_subset_ids=[f"q{i:03d}" for i in range(30)],
        judge_variance_runs=variance_runs if variance_runs is not None else [0.70, 0.73, 0.68],
        deviation_notes=deviation_notes
        if deviation_notes is not None
        else build_deviation_notes(judge_model, "ollama/nomic-embed-text", seed),
    )


def _make_judge_with_mock(raw_response: str) -> OllamaLMEJudge:
    """Return an OllamaLMEJudge whose ollama.generate is mocked to return *raw_response*."""
    with patch.dict("sys.modules", {"ollama": MagicMock()}):
        judge = OllamaLMEJudge.__new__(OllamaLMEJudge)
        judge.model = "llama3.1:8b"
        judge._host = None
        judge.seed = 42
    return judge


# ---------------------------------------------------------------------------
# Verdict parsing — benchmark_micro (no Ollama)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_judge_parses_correct() -> None:
    """Ollama response 'CORRECT' is parsed as True."""
    assert OllamaLMEJudge._parse_verdict("CORRECT") is True


@pytest.mark.benchmark_micro
def test_judge_parses_incorrect() -> None:
    """Ollama response 'INCORRECT' is parsed as False."""
    assert OllamaLMEJudge._parse_verdict("INCORRECT") is False


@pytest.mark.benchmark_micro
def test_judge_strips_think_tags() -> None:
    """<think>…</think> blocks are stripped before parsing (deepseek-r1 compat)."""
    raw = "<think>step-by-step reasoning here</think>\nCORRECT"
    assert OllamaLMEJudge._parse_verdict(raw) is True


@pytest.mark.benchmark_micro
def test_judge_strips_multiline_think_tags() -> None:
    """Multi-line <think>…</think> blocks are stripped correctly."""
    raw = "<think>\nline one\nline two\n</think>\nINCORRECT"
    assert OllamaLMEJudge._parse_verdict(raw) is False


@pytest.mark.benchmark_micro
def test_judge_handles_ambiguous_response() -> None:
    """Response containing neither CORRECT nor INCORRECT defaults to False."""
    assert OllamaLMEJudge._parse_verdict("I'm not sure about this one.") is False


@pytest.mark.benchmark_micro
def test_judge_handles_empty_response() -> None:
    """Empty response defaults to False."""
    assert OllamaLMEJudge._parse_verdict("") is False


@pytest.mark.benchmark_micro
def test_judge_incorrect_takes_priority_over_embedded_correct() -> None:
    """'INCORRECT' is parsed as False even though it contains 'CORRECT' as substring."""
    # "INCORRECT" starts with "INCO" so the only "CORRECT" is inside "INCORRECT".
    assert OllamaLMEJudge._parse_verdict("INCORRECT") is False


@pytest.mark.benchmark_micro
def test_judge_case_insensitive_correct() -> None:
    """Lowercase 'correct' is recognised."""
    assert OllamaLMEJudge._parse_verdict("correct") is True


@pytest.mark.benchmark_micro
def test_judge_case_insensitive_incorrect() -> None:
    """Lowercase 'incorrect' is recognised."""
    assert OllamaLMEJudge._parse_verdict("incorrect") is False


# ---------------------------------------------------------------------------
# OllamaLMEJudge.judge() — mocked Ollama call
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_judge_method_returns_true_on_correct() -> None:
    """OllamaLMEJudge.judge() returns (True, raw) when model says CORRECT."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "CORRECT"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaLMEJudge(model="llama3.1:8b", seed=42)
        is_correct, raw = judge.judge(
            question="What colour is the sky?",
            gold_answer="Blue",
            retrieved_context="The sky is blue.",
        )

    assert is_correct is True
    assert raw == "CORRECT"
    mock_ollama.generate.assert_called_once()


@pytest.mark.benchmark_micro
def test_judge_method_returns_false_on_incorrect() -> None:
    """OllamaLMEJudge.judge() returns (False, raw) when model says INCORRECT."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "INCORRECT"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaLMEJudge(model="llama3.1:8b", seed=42)
        is_correct, raw = judge.judge(
            question="What colour is the sky?",
            gold_answer="Blue",
            retrieved_context="The sky is green.",
        )

    assert is_correct is False
    assert raw == "INCORRECT"


@pytest.mark.benchmark_micro
def test_judge_with_custom_host_uses_client() -> None:
    """OllamaLMEJudge uses ollama.Client when host is provided."""
    mock_ollama = MagicMock()
    mock_client = MagicMock()
    mock_client.generate.return_value = {"response": "CORRECT"}
    mock_ollama.Client.return_value = mock_client

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaLMEJudge(model="llama3.1:8b", host="http://custom:11434", seed=42)
        is_correct, _ = judge.judge("q?", "a", "retrieved text")

    assert is_correct is True
    mock_ollama.Client.assert_called_once_with(host="http://custom:11434")
    mock_client.generate.assert_called_once()


# ---------------------------------------------------------------------------
# Markdown formatting — benchmark_micro (no I/O)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_format_markdown_includes_all_categories() -> None:
    """All 6 LongMemEval categories appear in the formatted output."""
    result = _make_result()
    md = format_benchmark_run_markdown(result, "2026-09-01")
    for cat in LME_CATEGORIES:
        assert cat in md, f"Category {cat!r} missing from formatted markdown"


@pytest.mark.benchmark_micro
def test_format_markdown_lists_deviations() -> None:
    """Deviation notes appear verbatim in the formatted output."""
    notes = [
        "Judge model is 'llama3.1:8b', not 'GPT-4o' (published benchmark uses GPT-4o)",
        "Dataset is the real LongMemEval longmemeval_s (500 questions, Apache-2.0) "
        "— methodology comparable in kind to published figures but NOT apples-to-apples",
        "Seed stamped on every run: 42 (for reproducibility)",
    ]
    result = _make_result(deviation_notes=notes)
    md = format_benchmark_run_markdown(result, "2026-09-01")
    for note in notes:
        assert note in md, f"Deviation note missing: {note!r}"


@pytest.mark.benchmark_micro
def test_judge_variance_section_present() -> None:
    """format_benchmark_run_markdown includes a 'Judge variance' section."""
    result = _make_result(variance_runs=[0.70, 0.73, 0.68])
    md = format_benchmark_run_markdown(result, "2026-09-01")
    assert "Judge variance" in md
    assert "Spread" in md


@pytest.mark.benchmark_micro
def test_format_markdown_no_variance_runs() -> None:
    """Missing variance runs produce a graceful placeholder in the markdown."""
    result = _make_result(variance_runs=[])
    md = format_benchmark_run_markdown(result, "2026-09-01")
    assert "Judge variance" in md
    assert "Not measured" in md


@pytest.mark.benchmark_micro
def test_format_markdown_overall_accuracy_present() -> None:
    """Overall accuracy row is present in the formatted table."""
    result = _make_result()
    md = format_benchmark_run_markdown(result, "2026-09-01")
    assert "**Overall**" in md


@pytest.mark.benchmark_micro
def test_format_markdown_stamped_fields() -> None:
    """Run id, judge model, seed, top_k, and split are all stamped in the output."""
    result = _make_result(judge_model="deepseek-r1:8b", seed=99)
    md = format_benchmark_run_markdown(result, "2026-09-01")
    assert result.run_id in md
    assert "deepseek-r1:8b" in md
    assert "99" in md
    assert str(result.top_k) in md
    assert result.split in md


@pytest.mark.benchmark_micro
def test_format_markdown_never_apples_to_apples() -> None:
    """The formatted output explicitly disclaims apples-to-apples comparison."""
    result = _make_result()
    md = format_benchmark_run_markdown(result, "2026-09-01")
    # Must contain an explicit disclaimer — not just note it differently.
    assert "NOT apples-to-apples" in md or "not apples-to-apples" in md.lower()


# ---------------------------------------------------------------------------
# append_to_benchmarks_md — benchmark_micro (temp file I/O)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_append_does_not_modify_existing_content() -> None:
    """append_to_benchmarks_md preserves all existing sentinel content and adds new section."""
    sentinel = "## SENTINEL CONTENT — must not be removed or altered\n\nRun A existing data.\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "BENCHMARKS.md"
        path.write_text(sentinel, encoding="utf-8")

        result = _make_result()
        append_to_benchmarks_md(result, path)

        updated = path.read_text(encoding="utf-8")

    # Sentinel must still be present, unchanged.
    assert sentinel.strip() in updated, "Existing sentinel content was modified or removed"
    # New section must appear after the sentinel.
    sentinel_pos = updated.find(sentinel.strip())
    new_section_pos = updated.find("### Run E")
    assert new_section_pos > sentinel_pos, (
        "New run section appears before the existing content — "
        "existing runs may have been overwritten"
    )


@pytest.mark.benchmark_micro
def test_append_new_section_contains_run_id() -> None:
    """Appended section contains the result's run_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "BENCHMARKS.md"
        path.write_text("# Benchmark Report\n\nExisting content.\n", encoding="utf-8")

        result = _make_result()
        append_to_benchmarks_md(result, path)

        updated = path.read_text(encoding="utf-8")

    assert result.run_id in updated


@pytest.mark.benchmark_micro
def test_append_idempotent_for_two_runs() -> None:
    """Appending two different runs adds two sections; neither overwrites the other."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "BENCHMARKS.md"
        path.write_text("# Header\n", encoding="utf-8")

        result1 = _make_result()
        result2 = _make_result()
        append_to_benchmarks_md(result1, path)
        append_to_benchmarks_md(result2, path)

        updated = path.read_text(encoding="utf-8")

    assert result1.run_id in updated
    assert result2.run_id in updated
    # Both run ids appear independently — second append didn't overwrite first.
    assert updated.count("### Run E") == 2


# ---------------------------------------------------------------------------
# build_deviation_notes — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_deviation_notes_non_gpt4o_judge() -> None:
    """build_deviation_notes flags non-GPT-4o judge model."""
    notes = build_deviation_notes("llama3.1:8b", "ollama/nomic-embed-text", 42)
    joined = " ".join(notes)
    assert "GPT-4o" in joined
    assert "llama3.1:8b" in joined


@pytest.mark.benchmark_micro
def test_deviation_notes_seed_always_present() -> None:
    """build_deviation_notes always includes the seed note."""
    notes = build_deviation_notes("llama3.1:8b", "ollama/nomic-embed-text", 77)
    joined = " ".join(notes)
    assert "77" in joined
    assert "seed" in joined.lower()


@pytest.mark.benchmark_micro
def test_deviation_notes_dataset_always_present() -> None:
    """build_deviation_notes always includes the dataset methodology note."""
    notes = build_deviation_notes("llama3.1:8b", "ollama/nomic-embed-text", 42)
    joined = " ".join(notes)
    assert "longmemeval_s" in joined
    assert "500 questions" in joined

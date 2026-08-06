"""
benchmarks/agent_quality/test_groundedness.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
AGQ-3 unit tests — no live Db2, no live Ollama required.

Coverage
--------
* ``OllamaGroundednessJudge._parse_score`` — all documented patterns plus
  edge cases (think-tag stripping, ambiguous → default 3).
* ``OllamaGroundednessJudge.judge()`` — mocked Ollama generate call.
* ``GroundednessRunResult.to_dict()`` — UNI-3 JSON shape.
* ``_aggregate()`` — per-category means, overall mean, delta.
* ``run_groundedness()`` — full run with mocked store, judge, and dataset.

All tests are ``benchmark_micro`` — no external services required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from benchmarks.agent_quality.groundedness import (
    GROUNDEDNESS_JUDGE_VERSION,
    GroundednessResult,
    GroundednessRunResult,
    OllamaGroundednessJudge,
    _aggregate,
    run_groundedness,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(
    question_id: str = "q001",
    category: str = "single-session-user",
    score: int = 4,
    no_memory_score: int = 2,
) -> GroundednessResult:
    """Build a minimal GroundednessResult for aggregation tests."""
    return GroundednessResult(
        question_id=question_id,
        category=category,
        score=score,
        no_memory_score=no_memory_score,
        delta=score - no_memory_score,
        raw_response="raw",
        raw_no_memory_response="raw_no_mem",
    )


def _make_row(
    question_id: str = "q001",
    category: str = "single-session-user",
    question: str = "What does Alice like?",
    answer: str = "Alice likes hiking.",
) -> dict[str, Any]:
    """Build a minimal LongMemEval-shaped row dict — mirrors test_longmemeval_adapter."""
    return {
        "question_id": question_id,
        "question_type": category,
        "question": question,
        "answer": answer,
        "haystack_sessions": [
            {
                "session_id": "sess-0",
                "turns": [
                    {"role": "user", "content": "Alice enjoys hiking in the mountains."},
                    {"role": "assistant", "content": "Got it."},
                ],
            }
        ],
        "evidence_session_ids": ["sess-0"],
    }


# ---------------------------------------------------------------------------
# _parse_score — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_parse_score_explicit_score_colon() -> None:
    """'Score: 4' is parsed as 4."""
    assert OllamaGroundednessJudge._parse_score("Score: 4") == 4


@pytest.mark.benchmark_micro
def test_parse_score_explicit_score_slash() -> None:
    """'4/5' is parsed as 4."""
    assert OllamaGroundednessJudge._parse_score("The answer scores 4/5.") == 4


@pytest.mark.benchmark_micro
def test_parse_score_rate_phrase() -> None:
    """'I would rate this 3' is parsed as 3."""
    assert OllamaGroundednessJudge._parse_score("I would rate this 3 out of 5.") == 3


@pytest.mark.benchmark_micro
def test_parse_score_think_stripped_before_parsing() -> None:
    """<think>…</think> block is stripped; score after it is parsed."""
    raw = "<think>Reasoning here about the claims.</think>\nScore: 5"
    assert OllamaGroundednessJudge._parse_score(raw) == 5


@pytest.mark.benchmark_micro
def test_parse_score_multiline_think_stripped() -> None:
    """Multi-line <think> block is stripped; bare integer after it is used."""
    raw = "<think>\nline one\nline two\n</think>\n\n4"
    assert OllamaGroundednessJudge._parse_score(raw) == 4


@pytest.mark.benchmark_micro
def test_parse_score_ambiguous_returns_default() -> None:
    """Text with no recognisable score pattern defaults to 3."""
    assert OllamaGroundednessJudge._parse_score("The answer is partially good.") == 3


@pytest.mark.benchmark_micro
def test_parse_score_empty_returns_default() -> None:
    """Empty string defaults to 3."""
    assert OllamaGroundednessJudge._parse_score("") == 3


@pytest.mark.benchmark_micro
def test_parse_score_out_of_range_integer_ignored() -> None:
    """An integer outside [1, 5] is not accepted; falls through to default."""
    # "Score: 7" — 7 is out-of-range; no other pattern matches; default 3.
    assert OllamaGroundednessJudge._parse_score("Score: 7") == 3


@pytest.mark.benchmark_micro
def test_parse_score_boundary_1() -> None:
    """Score: 1 is the minimum valid value."""
    assert OllamaGroundednessJudge._parse_score("Score: 1") == 1


@pytest.mark.benchmark_micro
def test_parse_score_boundary_5() -> None:
    """Score: 5 is the maximum valid value."""
    assert OllamaGroundednessJudge._parse_score("Score: 5") == 5


@pytest.mark.benchmark_micro
def test_parse_score_case_insensitive() -> None:
    """'SCORE: 2' (uppercase) is recognised."""
    assert OllamaGroundednessJudge._parse_score("SCORE: 2") == 2


@pytest.mark.benchmark_micro
def test_parse_score_rating_label() -> None:
    """'Rating: 4' is parsed as 4."""
    assert OllamaGroundednessJudge._parse_score("Rating: 4") == 4


# ---------------------------------------------------------------------------
# OllamaGroundednessJudge.judge() — mocked Ollama
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_judge_returns_score_and_raw() -> None:
    """judge() returns (score, raw_response) when model says 'Score: 4'."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "The answer is supported. Score: 4"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaGroundednessJudge(model="llama3.1:8b", seed=42)
        score, raw = judge.judge(
            question="What does Alice like?",
            retrieved_context="Alice enjoys hiking in the mountains.",
            generated_answer="Alice likes hiking.",
        )

    assert score == 4
    assert "Score: 4" in raw
    mock_ollama.generate.assert_called_once()


@pytest.mark.benchmark_micro
def test_judge_with_custom_host_uses_client() -> None:
    """judge() uses ollama.Client when host is provided."""
    mock_ollama = MagicMock()
    mock_client = MagicMock()
    mock_client.generate.return_value = {"response": "Score: 5"}
    mock_ollama.Client.return_value = mock_client

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaGroundednessJudge(
            model="llama3.1:8b", host="http://custom:11434", seed=42
        )
        score, _ = judge.judge("q?", "context", "answer")

    assert score == 5
    mock_ollama.Client.assert_called_once_with(host="http://custom:11434")
    mock_client.generate.assert_called_once()


@pytest.mark.benchmark_micro
def test_judge_empty_context_returns_score() -> None:
    """judge() with empty retrieved_context still returns a valid score."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "Score: 1"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaGroundednessJudge(model="llama3.1:8b", seed=42)
        score, _ = judge.judge(
            question="What does Alice like?",
            retrieved_context="",
            generated_answer="Alice likes hiking.",
        )

    assert score == 1


@pytest.mark.benchmark_micro
def test_judge_prompt_contains_all_variables() -> None:
    """The judge prompt sent to Ollama contains question, context, and answer."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "Score: 3"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaGroundednessJudge(model="llama3.1:8b", seed=42)
        judge.judge(
            question="What colour is the sky?",
            retrieved_context="The sky is blue on clear days.",
            generated_answer="The sky is blue.",
        )

    call_kwargs = mock_ollama.generate.call_args
    prompt_sent: str = call_kwargs[1].get("prompt") or call_kwargs[0][1]
    assert "What colour is the sky?" in prompt_sent
    assert "The sky is blue on clear days." in prompt_sent
    assert "The sky is blue." in prompt_sent


# ---------------------------------------------------------------------------
# GroundednessRunResult.to_dict() — UNI-3 shape
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_to_dict_shape() -> None:
    """to_dict() produces the UNI-3 scorecard JSON shape."""
    run_result = GroundednessRunResult(
        groundedness_mean=3.8,
        groundedness_per_category={"single-session-user": 4.2, "knowledge-update": 3.1},
        no_memory_mean=2.1,
        delta_mean=1.7,
        judge_model="llama3.1:8b",
        judge_version=GROUNDEDNESS_JUDGE_VERSION,
        seed=42,
    )
    d = run_result.to_dict()
    assert d["groundedness_mean"] == 3.8
    assert d["no_memory_mean"] == 2.1
    assert d["delta_mean"] == 1.7
    assert d["judge_model"] == "llama3.1:8b"
    assert d["judge_version"] == GROUNDEDNESS_JUDGE_VERSION
    assert d["seed"] == 42
    assert "groundedness_per_category" in d


@pytest.mark.benchmark_micro
def test_to_dict_does_not_include_per_question() -> None:
    """to_dict() omits the per_question list (scorecard format only)."""
    run_result = GroundednessRunResult(
        groundedness_mean=3.0,
        groundedness_per_category={},
        no_memory_mean=2.0,
        delta_mean=1.0,
        judge_model="llama3.1:8b",
        judge_version=GROUNDEDNESS_JUDGE_VERSION,
        seed=42,
        per_question=[_make_result()],
    )
    d = run_result.to_dict()
    assert "per_question" not in d


# ---------------------------------------------------------------------------
# _aggregate — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_aggregate_empty_returns_zeros() -> None:
    """_aggregate with no results returns zero-valued GroundednessRunResult."""
    result = _aggregate(per_question=[], judge_model="llama3.1:8b", seed=42)
    assert result.groundedness_mean == 0.0
    assert result.no_memory_mean == 0.0
    assert result.delta_mean == 0.0
    assert result.groundedness_per_category == {}


@pytest.mark.benchmark_micro
def test_aggregate_overall_mean() -> None:
    """_aggregate computes the correct overall mean score."""
    results = [
        _make_result("q1", score=4, no_memory_score=2),
        _make_result("q2", score=2, no_memory_score=2),
    ]
    agg = _aggregate(results, judge_model="llama3.1:8b", seed=42)
    assert agg.groundedness_mean == pytest.approx(3.0)


@pytest.mark.benchmark_micro
def test_aggregate_delta_mean() -> None:
    """_aggregate computes delta as (with-memory score) − (no-memory score)."""
    results = [
        _make_result("q1", score=5, no_memory_score=2),   # delta = 3
        _make_result("q2", score=3, no_memory_score=1),   # delta = 2
    ]
    agg = _aggregate(results, judge_model="llama3.1:8b", seed=42)
    assert agg.delta_mean == pytest.approx(2.5)


@pytest.mark.benchmark_micro
def test_aggregate_no_memory_mean() -> None:
    """_aggregate computes the no-memory mean correctly."""
    results = [
        _make_result("q1", score=4, no_memory_score=1),
        _make_result("q2", score=4, no_memory_score=3),
    ]
    agg = _aggregate(results, judge_model="llama3.1:8b", seed=42)
    assert agg.no_memory_mean == pytest.approx(2.0)


@pytest.mark.benchmark_micro
def test_aggregate_per_category_means() -> None:
    """_aggregate computes per-category means correctly."""
    results = [
        _make_result("q1", category="single-session-user", score=4, no_memory_score=2),
        _make_result("q2", category="single-session-user", score=2, no_memory_score=2),
        _make_result("q3", category="knowledge-update",    score=5, no_memory_score=2),
    ]
    agg = _aggregate(results, judge_model="llama3.1:8b", seed=42)
    assert agg.groundedness_per_category["single-session-user"] == pytest.approx(3.0)
    assert agg.groundedness_per_category["knowledge-update"] == pytest.approx(5.0)


@pytest.mark.benchmark_micro
def test_aggregate_stamps_judge_model_and_version() -> None:
    """_aggregate stamps judge_model and GROUNDEDNESS_JUDGE_VERSION on the result."""
    agg = _aggregate(
        per_question=[_make_result()],
        judge_model="deepseek-r1:8b",
        seed=99,
    )
    assert agg.judge_model == "deepseek-r1:8b"
    assert agg.judge_version == GROUNDEDNESS_JUDGE_VERSION
    assert agg.seed == 99


@pytest.mark.benchmark_micro
def test_aggregate_per_question_preserved() -> None:
    """_aggregate preserves the per_question list on the result."""
    pq = [_make_result("q1"), _make_result("q2")]
    agg = _aggregate(pq, judge_model="llama3.1:8b", seed=42)
    assert len(agg.per_question) == 2


# ---------------------------------------------------------------------------
# run_groundedness — fully mocked (no Db2, no Ollama)
# ---------------------------------------------------------------------------


def _make_mock_store(search_content: str = "Alice enjoys hiking.") -> MagicMock:
    """Return a MagicMock store that returns a single SearchResult-like object."""
    mock_result = MagicMock()
    mock_result.content = search_content
    store = MagicMock()
    store.search.return_value = [mock_result]
    store.add_messages.return_value = ["id-001"]
    return store


def _make_mock_judge(with_score: int = 4, no_score: int = 2) -> MagicMock:
    """Return a MagicMock judge that returns fixed scores.

    The first call (with context) returns (with_score, "raw"),
    subsequent calls with empty context return (no_score, "raw_no").
    """
    judge = MagicMock(spec=OllamaGroundednessJudge)
    judge.model = "llama3.1:8b"

    def _side_effect(question: str, retrieved_context: str, generated_answer: str) -> tuple[int, str]:  # noqa: E501
        if retrieved_context and retrieved_context != "(none)":
            return with_score, f"Score: {with_score}"
        return no_score, f"Score: {no_score}"

    judge.judge.side_effect = _side_effect
    return judge


@pytest.mark.benchmark_micro
def test_run_groundedness_basic() -> None:
    """run_groundedness returns a GroundednessRunResult with correct means."""
    rows = [_make_row(question_id=f"q{i}", category="single-session-user") for i in range(3)]

    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "Score: 4"}

    store = _make_mock_store()
    judge = _make_mock_judge(with_score=4, no_score=2)

    with (
        patch(
            "benchmarks.agent_quality.groundedness.load_longmemeval",
            return_value=rows,
        ),
        patch.dict("sys.modules", {"ollama": mock_ollama}),
    ):
        result = run_groundedness(
            store=store,
            embedding_provider=MagicMock(),
            embedding_provider_name="mock",
            judge=judge,
            judge_name="llama3.1:8b",
            n_per_category=5,
            seed=42,
            top_k=3,
        )

    assert isinstance(result, GroundednessRunResult)
    assert result.groundedness_mean == pytest.approx(4.0)
    assert result.no_memory_mean == pytest.approx(2.0)
    assert result.delta_mean == pytest.approx(2.0)
    assert "single-session-user" in result.groundedness_per_category


@pytest.mark.benchmark_micro
def test_run_groundedness_n_per_category_cap() -> None:
    """run_groundedness caps questions at n_per_category per category."""
    # 10 rows in the same category; only 3 should be processed.
    rows = [_make_row(question_id=f"q{i}", category="abstention") for i in range(10)]

    store = _make_mock_store()
    judge = _make_mock_judge(with_score=3, no_score=3)

    with patch(
        "benchmarks.agent_quality.groundedness.load_longmemeval",
        return_value=rows,
    ):
        result = run_groundedness(
            store=store,
            embedding_provider=MagicMock(),
            embedding_provider_name="mock",
            judge=judge,
            judge_name="llama3.1:8b",
            n_per_category=3,
            seed=42,
        )

    assert len(result.per_question) == 3


@pytest.mark.benchmark_micro
def test_run_groundedness_delta_computed_per_question() -> None:
    """run_groundedness computes delta as with_score − no_score per question."""
    rows = [_make_row(question_id="q1", category="multi-session")]

    store = _make_mock_store()
    judge = _make_mock_judge(with_score=5, no_score=2)

    with patch(
        "benchmarks.agent_quality.groundedness.load_longmemeval",
        return_value=rows,
    ):
        result = run_groundedness(
            store=store,
            embedding_provider=MagicMock(),
            embedding_provider_name="mock",
            judge=judge,
            judge_name="llama3.1:8b",
            n_per_category=5,
            seed=42,
        )

    assert result.delta_mean == pytest.approx(3.0)
    assert result.per_question[0].delta == 3


@pytest.mark.benchmark_micro
def test_run_groundedness_erase_called_per_question() -> None:
    """run_groundedness calls store.erase_all() once per successfully processed question."""
    rows = [_make_row(question_id=f"q{i}", category="temporal-reasoning") for i in range(2)]

    store = _make_mock_store()
    judge = _make_mock_judge()

    with patch(
        "benchmarks.agent_quality.groundedness.load_longmemeval",
        return_value=rows,
    ):
        run_groundedness(
            store=store,
            embedding_provider=MagicMock(),
            embedding_provider_name="mock",
            judge=judge,
            judge_name="llama3.1:8b",
            n_per_category=5,
            seed=42,
        )

    assert store.erase_all.call_count == 2


@pytest.mark.benchmark_micro
def test_run_groundedness_multi_category_aggregation() -> None:
    """run_groundedness aggregates across multiple categories correctly."""
    rows = [
        _make_row("q0", category="single-session-user"),
        _make_row("q1", category="single-session-user"),
        _make_row("q2", category="knowledge-update"),
    ]

    store = _make_mock_store()

    call_counter: dict[str, int] = {"n": 0}

    def _judge_side_effect(question: str, retrieved_context: str, generated_answer: str) -> tuple[int, str]:  # noqa: E501
        call_counter["n"] += 1
        if retrieved_context and retrieved_context != "(none)":
            return 4, "Score: 4"
        return 2, "Score: 2"

    judge = MagicMock(spec=OllamaGroundednessJudge)
    judge.judge.side_effect = _judge_side_effect

    with patch(
        "benchmarks.agent_quality.groundedness.load_longmemeval",
        return_value=rows,
    ):
        result = run_groundedness(
            store=store,
            embedding_provider=MagicMock(),
            embedding_provider_name="mock",
            judge=judge,
            judge_name="llama3.1:8b",
            n_per_category=5,
            seed=42,
        )

    assert result.groundedness_per_category["single-session-user"] == pytest.approx(4.0)
    assert result.groundedness_per_category["knowledge-update"] == pytest.approx(4.0)
    assert len(result.per_question) == 3


@pytest.mark.benchmark_micro
def test_run_groundedness_to_dict_has_required_keys() -> None:
    """The dict from GroundednessRunResult.to_dict() has all UNI-3 required keys."""
    rows = [_make_row()]
    store = _make_mock_store()
    judge = _make_mock_judge()

    with patch(
        "benchmarks.agent_quality.groundedness.load_longmemeval",
        return_value=rows,
    ):
        result = run_groundedness(
            store=store,
            embedding_provider=MagicMock(),
            embedding_provider_name="mock",
            judge=judge,
            judge_name="llama3.1:8b",
            n_per_category=5,
            seed=42,
        )

    d = result.to_dict()
    required_keys = {
        "groundedness_mean",
        "groundedness_per_category",
        "no_memory_mean",
        "delta_mean",
        "judge_model",
        "judge_version",
        "seed",
    }
    missing = required_keys - set(d)
    assert not missing, f"UNI-3 required keys missing from to_dict(): {missing}"

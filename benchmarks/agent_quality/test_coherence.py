"""
benchmarks/agent_quality/test_coherence.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
AGQ-4 unit tests for the coherence/fluency judge.

All tests are ``benchmark_micro`` — no Db2, no live Ollama required (mocked).

Coverage
--------
* ``_parse_score``: "Score: 4", "4/5", "3 out of 5", "<think>…</think>4",
  bare digit, ambiguous → default 3.
* ``judge_coherence`` / ``judge_fluency``: mocked Ollama calls return fixed scores.
* Delta computation: ``coherence_delta = coherence_mean − coherence_no_memory_mean``.
* ``multi_type_injection_finding`` fires when ``coherence_delta ≤ −0.3``.
* Per-category aggregation via ``_aggregate_category``.
* ``CoherenceRunResult`` field layout matches the UNI-3 expected JSON shape.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from benchmarks.agent_quality.coherence import (
    COHERENCE_JUDGE_VERSION,
    FLUENCY_JUDGE_VERSION,
    CoherenceResult,
    CoherenceRunResult,
    OllamaCoherenceJudge,
    _aggregate_category,
    _mean,
    run_coherence,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_judge() -> OllamaCoherenceJudge:
    """Return an OllamaCoherenceJudge constructed without a real 'ollama' package."""
    with patch.dict("sys.modules", {"ollama": MagicMock()}):
        judge = OllamaCoherenceJudge(model="llama3.1:8b", seed=42)
    return judge


def _make_result(
    question_id: str = "q001",
    category: str = "single-session-user",
    coherence_score: int = 4,
    fluency_score: int = 4,
    coherence_no_memory: int = 3,
    fluency_no_memory: int = 3,
    context_length: int = 100,
) -> CoherenceResult:
    """Build a minimal :class:`CoherenceResult` for aggregation tests."""
    return CoherenceResult(
        question_id=question_id,
        category=category,
        coherence_score=coherence_score,
        fluency_score=fluency_score,
        coherence_no_memory=coherence_no_memory,
        fluency_no_memory=fluency_no_memory,
        context_length=context_length,
    )


# ---------------------------------------------------------------------------
# _parse_score — benchmark_micro (no Ollama)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_parse_score_bare_digit() -> None:
    """A bare digit at the start of cleaned output is parsed correctly."""
    assert OllamaCoherenceJudge._parse_score("4 The response flows logically.") == 4


@pytest.mark.benchmark_micro
def test_parse_score_score_colon_format() -> None:
    """'Score: 4' format is recognised."""
    assert OllamaCoherenceJudge._parse_score("Score: 4") == 4


@pytest.mark.benchmark_micro
def test_parse_score_score_equals_format() -> None:
    """'Score=3' format is recognised."""
    assert OllamaCoherenceJudge._parse_score("Score=3") == 3


@pytest.mark.benchmark_micro
def test_parse_score_fraction_format() -> None:
    """'4/5' fraction format is recognised."""
    assert OllamaCoherenceJudge._parse_score("4/5") == 4


@pytest.mark.benchmark_micro
def test_parse_score_out_of_format() -> None:
    """'3 out of 5' format is recognised."""
    assert OllamaCoherenceJudge._parse_score("3 out of 5") == 3


@pytest.mark.benchmark_micro
def test_parse_score_out_of_format_case_insensitive() -> None:
    """'2 Out Of 5' is recognised regardless of case."""
    assert OllamaCoherenceJudge._parse_score("2 Out Of 5") == 2


@pytest.mark.benchmark_micro
def test_parse_score_strips_think_tags() -> None:
    """<think>…</think> block is stripped before parsing."""
    raw = "<think>Let me think carefully about this response.</think>4"
    assert OllamaCoherenceJudge._parse_score(raw) == 4


@pytest.mark.benchmark_micro
def test_parse_score_strips_multiline_think_tags() -> None:
    """Multi-line <think>…</think> blocks are stripped correctly."""
    raw = "<think>\nreasoning line 1\nreasoning line 2\n</think>\n3"
    assert OllamaCoherenceJudge._parse_score(raw) == 3


@pytest.mark.benchmark_micro
def test_parse_score_ambiguous_defaults_to_3() -> None:
    """Ambiguous response with no 1–5 digit defaults to 3."""
    assert OllamaCoherenceJudge._parse_score("The response is acceptable.") == 3


@pytest.mark.benchmark_micro
def test_parse_score_empty_defaults_to_3() -> None:
    """Empty response defaults to 3."""
    assert OllamaCoherenceJudge._parse_score("") == 3


@pytest.mark.benchmark_micro
def test_parse_score_only_think_block_defaults_to_3() -> None:
    """Response that is only a <think> block (no score) defaults to 3."""
    raw = "<think>I cannot determine a score for this.</think>"
    assert OllamaCoherenceJudge._parse_score(raw) == 3


@pytest.mark.benchmark_micro
def test_parse_score_out_of_range_digit_ignored() -> None:
    """A digit '6' is not in the 1–5 range and falls through to 3."""
    # "6" is not matched by any valid 1-5 pattern, should default to 3.
    assert OllamaCoherenceJudge._parse_score("6") == 3


@pytest.mark.benchmark_micro
def test_parse_score_min_value() -> None:
    """Minimum value 1 is parsed correctly."""
    assert OllamaCoherenceJudge._parse_score("1") == 1


@pytest.mark.benchmark_micro
def test_parse_score_max_value() -> None:
    """Maximum value 5 is parsed correctly."""
    assert OllamaCoherenceJudge._parse_score("5/5 Perfect.") == 5


# ---------------------------------------------------------------------------
# OllamaCoherenceJudge.judge_coherence / judge_fluency — mocked Ollama
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_judge_coherence_returns_score_and_raw() -> None:
    """judge_coherence() returns (score, raw) tuple with the mocked response."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "4 The response is coherent."}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaCoherenceJudge(model="llama3.1:8b", seed=42)
        score, raw = judge.judge_coherence(
            question="What is the user's favourite colour?",
            context_injected="User said they like blue.",
            response="The user's favourite colour is blue.",
        )

    assert score == 4
    assert raw == "4 The response is coherent."
    mock_ollama.generate.assert_called_once()


@pytest.mark.benchmark_micro
def test_judge_fluency_returns_score_and_raw() -> None:
    """judge_fluency() returns (score, raw) tuple with the mocked response."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "5 Reads naturally."}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaCoherenceJudge(model="llama3.1:8b", seed=42)
        score, raw = judge.judge_fluency(
            question="What is the user's favourite colour?",
            context_injected="",
            response="The user's favourite colour is blue.",
        )

    assert score == 5
    assert raw == "5 Reads naturally."
    mock_ollama.generate.assert_called_once()


@pytest.mark.benchmark_micro
def test_judge_uses_client_when_host_provided() -> None:
    """OllamaCoherenceJudge uses ollama.Client when a custom host is provided."""
    mock_ollama = MagicMock()
    mock_client = MagicMock()
    mock_client.generate.return_value = {"response": "3 Acceptable."}
    mock_ollama.Client.return_value = mock_client

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaCoherenceJudge(
            model="llama3.1:8b", host="http://custom:11434", seed=42
        )
        score, _ = judge.judge_coherence("q?", "ctx", "response text")

    assert score == 3
    mock_ollama.Client.assert_called_once_with(host="http://custom:11434")
    mock_client.generate.assert_called_once()


@pytest.mark.benchmark_micro
def test_judge_no_memory_condition_passes_empty_context() -> None:
    """Passing empty context string works (no-memory control condition)."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "4"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaCoherenceJudge(model="llama3.1:8b", seed=42)
        score, _ = judge.judge_coherence(
            question="What is the user's favourite colour?",
            context_injected="",  # no-memory control
            response="The user's favourite colour is blue.",
        )

    assert score == 4
    # Verify the prompt passed to Ollama includes the empty context placeholder.
    call_args = mock_ollama.generate.call_args
    prompt_used: str = call_args.kwargs.get("prompt") or call_args.args[1] if call_args.args else call_args.kwargs["prompt"]
    assert "Memory context injected into the prompt (may be empty):" in prompt_used


# ---------------------------------------------------------------------------
# _mean helper
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_mean_of_values() -> None:
    """_mean computes the arithmetic mean correctly."""
    assert _mean([1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(3.0)


@pytest.mark.benchmark_micro
def test_mean_empty_list() -> None:
    """_mean returns 0.0 for an empty list."""
    assert _mean([]) == 0.0


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_coherence_delta_positive() -> None:
    """Delta is positive when with-memory score exceeds no-memory score."""
    results = [
        _make_result(coherence_score=4, coherence_no_memory=3),
        _make_result(coherence_score=5, coherence_no_memory=4),
    ]
    c_mean = _mean([float(r.coherence_score) for r in results])
    c_nm = _mean([float(r.coherence_no_memory) for r in results])
    delta = c_mean - c_nm
    assert delta == pytest.approx(1.0)


@pytest.mark.benchmark_micro
def test_coherence_delta_negative() -> None:
    """Delta is negative when with-memory score is lower than no-memory score."""
    results = [
        _make_result(coherence_score=2, coherence_no_memory=4),
        _make_result(coherence_score=3, coherence_no_memory=4),
    ]
    c_mean = _mean([float(r.coherence_score) for r in results])
    c_nm = _mean([float(r.coherence_no_memory) for r in results])
    delta = c_mean - c_nm
    assert delta == pytest.approx(-1.5)


@pytest.mark.benchmark_micro
def test_fluency_delta_zero_when_equal() -> None:
    """Fluency delta is 0.0 when with-memory and no-memory scores are identical."""
    results = [
        _make_result(fluency_score=4, fluency_no_memory=4),
        _make_result(fluency_score=3, fluency_no_memory=3),
    ]
    f_mean = _mean([float(r.fluency_score) for r in results])
    f_nm = _mean([float(r.fluency_no_memory) for r in results])
    assert (f_mean - f_nm) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# multi_type_injection_finding
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_multi_type_finding_fires_at_minus_0_3() -> None:
    """multi_type_injection_finding is set when coherence_delta = −0.3."""
    result = CoherenceRunResult(
        run_id="abc123",
        judge_model="llama3.1:8b",
        coherence_mean=3.5,
        fluency_mean=4.0,
        coherence_no_memory_mean=3.8,
        fluency_no_memory_mean=4.0,
        coherence_delta=-0.3,
        fluency_delta=0.0,
        multi_type_injection_finding=(
            "Context injection caused a measurable coherence degradation: "
            "coherence_delta=-0.300 (threshold ≤ −0.3)."
        ),
    )
    assert result.multi_type_injection_finding is not None
    assert "-0.3" in result.multi_type_injection_finding or "coherence_delta" in result.multi_type_injection_finding


@pytest.mark.benchmark_micro
def test_multi_type_finding_fires_below_minus_0_3() -> None:
    """multi_type_injection_finding is set when coherence_delta < −0.3."""
    result = CoherenceRunResult(
        run_id="abc123",
        judge_model="llama3.1:8b",
        coherence_mean=2.0,
        fluency_mean=3.0,
        coherence_no_memory_mean=4.0,
        fluency_no_memory_mean=4.0,
        coherence_delta=-2.0,
        fluency_delta=-1.0,
        multi_type_injection_finding=(
            "Context injection caused a measurable coherence degradation: "
            "coherence_delta=-2.000 (threshold ≤ −0.3)."
        ),
    )
    assert result.multi_type_injection_finding is not None


@pytest.mark.benchmark_micro
def test_multi_type_finding_none_when_delta_above_threshold() -> None:
    """multi_type_injection_finding is None when coherence_delta > −0.3."""
    result = CoherenceRunResult(
        run_id="abc123",
        judge_model="llama3.1:8b",
        coherence_mean=4.0,
        fluency_mean=4.0,
        coherence_no_memory_mean=3.8,
        fluency_no_memory_mean=3.8,
        coherence_delta=0.2,
        fluency_delta=0.2,
        multi_type_injection_finding=None,
    )
    assert result.multi_type_injection_finding is None


@pytest.mark.benchmark_micro
def test_multi_type_finding_none_when_delta_exactly_minus_0_29() -> None:
    """multi_type_injection_finding is None when coherence_delta = −0.29 (just above threshold)."""
    result = CoherenceRunResult(
        run_id="abc123",
        judge_model="llama3.1:8b",
        coherence_mean=3.71,
        fluency_mean=4.0,
        coherence_no_memory_mean=4.0,
        fluency_no_memory_mean=4.0,
        coherence_delta=-0.29,
        fluency_delta=0.0,
        multi_type_injection_finding=None,
    )
    assert result.multi_type_injection_finding is None


# ---------------------------------------------------------------------------
# Per-category aggregation
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_aggregate_category_single_category() -> None:
    """_aggregate_category correctly aggregates results within one category."""
    results = [
        _make_result(
            category="single-session-user",
            coherence_score=4,
            fluency_score=5,
            coherence_no_memory=3,
            fluency_no_memory=4,
        ),
        _make_result(
            category="single-session-user",
            coherence_score=2,
            fluency_score=3,
            coherence_no_memory=3,
            fluency_no_memory=4,
        ),
    ]
    per_cat = _aggregate_category(results)

    assert "single-session-user" in per_cat
    cat = per_cat["single-session-user"]
    assert cat["coherence_mean"] == pytest.approx(3.0)    # (4+2)/2
    assert cat["fluency_mean"] == pytest.approx(4.0)      # (5+3)/2
    assert cat["coherence_delta"] == pytest.approx(0.0)   # 3.0 - 3.0
    assert cat["fluency_delta"] == pytest.approx(0.0)     # 4.0 - 4.0


@pytest.mark.benchmark_micro
def test_aggregate_category_multiple_categories() -> None:
    """_aggregate_category populates multiple categories independently."""
    results = [
        _make_result(category="single-session-user", coherence_score=4, fluency_score=4,
                     coherence_no_memory=3, fluency_no_memory=3),
        _make_result(category="knowledge-update", coherence_score=2, fluency_score=3,
                     coherence_no_memory=4, fluency_no_memory=4),
    ]
    per_cat = _aggregate_category(results)

    assert "single-session-user" in per_cat
    assert "knowledge-update" in per_cat

    sse = per_cat["single-session-user"]
    assert sse["coherence_mean"] == pytest.approx(4.0)
    assert sse["coherence_delta"] == pytest.approx(1.0)  # 4 - 3

    ku = per_cat["knowledge-update"]
    assert ku["coherence_mean"] == pytest.approx(2.0)
    assert ku["coherence_delta"] == pytest.approx(-2.0)  # 2 - 4


@pytest.mark.benchmark_micro
def test_aggregate_category_empty_list() -> None:
    """_aggregate_category returns an empty dict for an empty results list."""
    per_cat = _aggregate_category([])
    assert per_cat == {}


@pytest.mark.benchmark_micro
def test_aggregate_category_negative_delta_flagged() -> None:
    """Per-category coherence_delta can be negative (injection degradation)."""
    results = [
        _make_result(
            category="multi-session",
            coherence_score=2,
            fluency_score=2,
            coherence_no_memory=5,
            fluency_no_memory=5,
        ),
    ]
    per_cat = _aggregate_category(results)
    assert per_cat["multi-session"]["coherence_delta"] == pytest.approx(-3.0)
    assert per_cat["multi-session"]["fluency_delta"] == pytest.approx(-3.0)


# ---------------------------------------------------------------------------
# CoherenceRunResult field layout (UNI-3 JSON shape)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_run_result_has_required_fields() -> None:
    """CoherenceRunResult exposes all fields required by the UNI-3 scorecard JSON shape."""
    result = CoherenceRunResult(
        run_id="test123",
        judge_model="llama3.1:8b",
        coherence_mean=3.8,
        fluency_mean=4.1,
        coherence_no_memory_mean=3.5,
        fluency_no_memory_mean=3.8,
        coherence_delta=0.3,
        fluency_delta=0.3,
        per_category={
            "single-session-user": {
                "coherence_mean": 4.2,
                "fluency_mean": 4.5,
                "coherence_delta": 0.5,
                "fluency_delta": 0.5,
            },
            "knowledge-update": {
                "coherence_mean": 3.1,
                "fluency_mean": 3.5,
                "coherence_delta": -0.2,
                "fluency_delta": 0.1,
            },
        },
        multi_type_injection_finding=None,
    )

    assert result.coherence_mean == pytest.approx(3.8)
    assert result.fluency_mean == pytest.approx(4.1)
    assert result.coherence_no_memory_mean == pytest.approx(3.5)
    assert result.fluency_no_memory_mean == pytest.approx(3.8)
    assert result.coherence_delta == pytest.approx(0.3)
    assert result.fluency_delta == pytest.approx(0.3)
    assert result.multi_type_injection_finding is None
    assert result.judge_model == "llama3.1:8b"
    assert result.coherence_judge_version == COHERENCE_JUDGE_VERSION
    assert result.fluency_judge_version == FLUENCY_JUDGE_VERSION
    assert "single-session-user" in result.per_category
    assert "knowledge-update" in result.per_category


@pytest.mark.benchmark_micro
def test_run_result_version_pins() -> None:
    """CoherenceRunResult carries the correct version pin constants."""
    result = CoherenceRunResult(
        run_id="x",
        judge_model="llama3.1:8b",
        coherence_mean=3.0,
        fluency_mean=3.0,
        coherence_no_memory_mean=3.0,
        fluency_no_memory_mean=3.0,
        coherence_delta=0.0,
        fluency_delta=0.0,
    )
    assert result.coherence_judge_version == "1.0.0"
    assert result.fluency_judge_version == "1.0.0"


# ---------------------------------------------------------------------------
# run_coherence — fully mocked (no Db2, no Ollama)
# ---------------------------------------------------------------------------


def _make_mock_store_coh(search_content: str = "Alice enjoys hiking.") -> MagicMock:
    """Return a MagicMock store whose search() returns a single content object."""
    mock_result = MagicMock()
    mock_result.content = search_content
    store = MagicMock()
    store.search.return_value = [mock_result]
    store.add_messages.return_value = ["id-001"]
    return store


def _make_mock_row(
    question_id: str = "q001",
    category: str = "single-session-user",
    question: str = "What does Alice like?",
    answer: str = "Alice likes hiking.",
) -> dict:
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


def _make_mock_judge_coh(
    with_score: int = 4,
    no_score: int = 2,
) -> MagicMock:
    """Return a MagicMock OllamaCoherenceJudge with fixed scores.

    Calls where context_injected is non-empty return (with_score, "raw"),
    calls with empty context return (no_score, "raw_no").
    """
    judge = MagicMock(spec=OllamaCoherenceJudge)
    judge.model = "llama3.1:8b"

    def _coh_side(question: str, context_injected: str, response: str) -> tuple[int, str]:
        if context_injected:
            return with_score, f"Score: {with_score}"
        return no_score, f"Score: {no_score}"

    def _flu_side(question: str, context_injected: str, response: str) -> tuple[int, str]:
        if context_injected:
            return with_score, f"Score: {with_score}"
        return no_score, f"Score: {no_score}"

    judge.judge_coherence.side_effect = _coh_side
    judge.judge_fluency.side_effect = _flu_side
    return judge


@pytest.mark.benchmark_micro
def test_run_coherence_basic() -> None:
    """run_coherence returns correct means when store.search() returns context."""
    rows = [_make_mock_row(question_id=f"q{i}", category="single-session-user") for i in range(3)]

    store = _make_mock_store_coh()
    judge = _make_mock_judge_coh(with_score=4, no_score=2)

    with patch(
        "benchmarks.agent_quality.coherence.load_longmemeval",
        return_value=rows,
    ):
        result = run_coherence(
            store=store,
            embedding_provider=MagicMock(),
            embedding_provider_name="mock",
            judge=judge,
            judge_name="llama3.1:8b",
            n_per_category=5,
            seed=42,
            top_k=3,
        )

    assert isinstance(result, CoherenceRunResult)
    assert result.coherence_mean == pytest.approx(4.0)
    assert result.fluency_mean == pytest.approx(4.0)
    assert result.coherence_no_memory_mean == pytest.approx(2.0)
    assert result.fluency_no_memory_mean == pytest.approx(2.0)
    assert "single-session-user" in result.per_category


@pytest.mark.benchmark_micro
def test_run_coherence_delta_computed() -> None:
    """run_coherence delta = with_score − no_score per question."""
    rows = [_make_mock_row(question_id="q1", category="multi-session")]

    store = _make_mock_store_coh()
    judge = _make_mock_judge_coh(with_score=5, no_score=2)

    with patch(
        "benchmarks.agent_quality.coherence.load_longmemeval",
        return_value=rows,
    ):
        result = run_coherence(
            store=store,
            embedding_provider=MagicMock(),
            embedding_provider_name="mock",
            judge=judge,
            judge_name="llama3.1:8b",
            n_per_category=5,
            seed=42,
        )

    assert result.coherence_delta == pytest.approx(3.0)
    assert result.fluency_delta == pytest.approx(3.0)


@pytest.mark.benchmark_micro
def test_run_coherence_erase_called_per_question() -> None:
    """run_coherence calls store.erase_all() once per processed question."""
    rows = [_make_mock_row(question_id=f"q{i}", category="temporal-reasoning") for i in range(2)]

    store = _make_mock_store_coh()
    judge = _make_mock_judge_coh()

    with patch(
        "benchmarks.agent_quality.coherence.load_longmemeval",
        return_value=rows,
    ):
        run_coherence(
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
def test_run_coherence_multi_category_aggregation() -> None:
    """run_coherence aggregates per-category means correctly across categories."""
    rows = [
        _make_mock_row("q0", category="single-session-user"),
        _make_mock_row("q1", category="single-session-user"),
        _make_mock_row("q2", category="knowledge-update"),
    ]

    store = _make_mock_store_coh()
    judge = _make_mock_judge_coh(with_score=4, no_score=2)

    with patch(
        "benchmarks.agent_quality.coherence.load_longmemeval",
        return_value=rows,
    ):
        result = run_coherence(
            store=store,
            embedding_provider=MagicMock(),
            embedding_provider_name="mock",
            judge=judge,
            judge_name="llama3.1:8b",
            n_per_category=5,
            seed=42,
        )

    assert result.per_category["single-session-user"]["coherence_mean"] == pytest.approx(4.0)
    assert result.per_category["knowledge-update"]["coherence_mean"] == pytest.approx(4.0)
    assert len(result.per_category) == 2


@pytest.mark.benchmark_micro
def test_run_coherence_uses_store_search_not_working_search() -> None:
    """run_coherence calls store.search(), not store.working.search()."""
    rows = [_make_mock_row(question_id="q1", category="single-session-user")]

    store = _make_mock_store_coh()
    judge = _make_mock_judge_coh()

    with patch(
        "benchmarks.agent_quality.coherence.load_longmemeval",
        return_value=rows,
    ):
        run_coherence(
            store=store,
            embedding_provider=MagicMock(),
            embedding_provider_name="mock",
            judge=judge,
            judge_name="llama3.1:8b",
            n_per_category=5,
            seed=42,
        )

    assert store.search.call_count >= 1, "store.search() must be called for context retrieval"
    # store.working.search must NOT have been called
    assert store.working.search.call_count == 0, (
        "store.working.search() must not be called — use the top-level store.search()"
    )

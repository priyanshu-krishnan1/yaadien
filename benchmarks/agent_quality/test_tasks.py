"""
benchmarks/agent_quality/test_tasks.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
AGQ-2 (EPIC-21) unit tests — all ``benchmark_micro`` (no Db2, mocked judge).

Coverage
--------
* Verdict parsing: SUCCESS → True, FAILURE → False, ambiguous → False
* deepseek-r1 <think>…</think> stripping in verdict parsing
* Pass¹ / Pass⁵ computation from known attempt vectors
* Task suite structure: 12 tasks, correct categories, required fields
* Three conditions execute correctly with a mocked judge
* with_sdk condition calls store.remember() / search() / erase_all()
* flat_context condition concatenates all prior session turns
* no_memory condition receives an empty context
* Abstention tasks succeed under no_memory (agent correctly asks)
* Non-abstention tasks fail under no_memory (agent has no context)
* AgentQualityResult.to_dict() emits the UNI-3 JSON shape
* OllamaAgentJudge calls Ollama correctly (mocked)
* Per-category Pass¹ breakdown is computed correctly
* build_task_suite() is seed-stable (same seed → same task seeds)
* run_agent_quality_suite() raises on bad condition
* run_agent_quality_suite() raises when with_sdk missing store/provider
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from benchmarks.agent_quality.tasks import (
    AGENT_QUALITY_CATEGORIES,
    CONDITIONS,
    N_ATTEMPTS,
    AgentQualityResult,
    AgentTask,
    OllamaAgentJudge,
    TaskResult,
    _build_flat_context,
    _compute_per_category,
    _parse_verdict,
    _simulate_agent_response,
    build_task_suite,
    compute_pass1_pass5,
    run_agent_quality_suite,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "t99",
    category: str = "knowledge-update",
    sessions: list[list[str]] | None = None,
    action_turn: str = "User: Do the thing.",
    required_memory: str = "The thing is X.",
    gold_action: str = "I'll do X.",
    seed: int = 0,
) -> AgentTask:
    """Build a minimal :class:`AgentTask` for unit tests."""
    if sessions is None:
        sessions = [["User: The thing is X.", "Agent: Noted, X."]]
    return AgentTask(
        task_id=task_id,
        category=category,
        description=f"Test task {task_id}",
        sessions=sessions,
        action_turn=action_turn,
        required_memory=required_memory,
        gold_action=gold_action,
        seed=seed,
    )


def _make_task_result(
    task_id: str = "t01",
    category: str = "knowledge-update",
    condition: str = "flat_context",
    attempts: list[bool] | None = None,
) -> TaskResult:
    """Build a :class:`TaskResult` with the given attempt vector."""
    if attempts is None:
        attempts = [True, True, False, True, True]
    pass1 = sum(attempts) / len(attempts)
    pass5 = all(attempts)
    return TaskResult(
        task_id=task_id,
        category=category,
        condition=condition,
        attempts=attempts,
        pass1=pass1,
        pass5=pass5,
    )


def _always_success_judge(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
    """Stub judge that always returns SUCCESS."""
    return True, "SUCCESS"


def _always_failure_judge(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
    """Stub judge that always returns FAILURE."""
    return False, "FAILURE"


# ---------------------------------------------------------------------------
# _parse_verdict — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_parse_verdict_success() -> None:
    """'SUCCESS' parses as True."""
    assert _parse_verdict("SUCCESS") is True


@pytest.mark.benchmark_micro
def test_parse_verdict_failure() -> None:
    """'FAILURE' parses as False."""
    assert _parse_verdict("FAILURE") is False


@pytest.mark.benchmark_micro
def test_parse_verdict_ambiguous_defaults_false() -> None:
    """Response containing neither keyword defaults to False."""
    assert _parse_verdict("I'm not sure.") is False


@pytest.mark.benchmark_micro
def test_parse_verdict_empty_defaults_false() -> None:
    """Empty response defaults to False."""
    assert _parse_verdict("") is False


@pytest.mark.benchmark_micro
def test_parse_verdict_case_insensitive_success() -> None:
    """Lowercase 'success' is recognised."""
    assert _parse_verdict("success") is True


@pytest.mark.benchmark_micro
def test_parse_verdict_case_insensitive_failure() -> None:
    """Lowercase 'failure' is recognised."""
    assert _parse_verdict("failure") is False


@pytest.mark.benchmark_micro
def test_parse_verdict_strips_think_tags() -> None:
    """<think>…</think> block is stripped before parsing."""
    raw = "<think>Step by step analysis here.</think>\nSUCCESS"
    assert _parse_verdict(raw) is True


@pytest.mark.benchmark_micro
def test_parse_verdict_strips_multiline_think_tags() -> None:
    """Multi-line <think>…</think> blocks are stripped correctly."""
    raw = "<think>\nline one\nline two\n</think>\nFAILURE"
    assert _parse_verdict(raw) is False


@pytest.mark.benchmark_micro
def test_parse_verdict_success_before_failure() -> None:
    """SUCCESS appearing before FAILURE is correctly parsed as True."""
    assert _parse_verdict("SUCCESS — the agent acted correctly. (not FAILURE)") is True


@pytest.mark.benchmark_micro
def test_parse_verdict_failure_before_success() -> None:
    """FAILURE appearing before SUCCESS is correctly parsed as False."""
    assert _parse_verdict("FAILURE (not SUCCESS)") is False


# ---------------------------------------------------------------------------
# compute_pass1_pass5 — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_compute_pass1_pass5_all_success() -> None:
    """All attempts succeeded → pass1=1.0, pass5=1.0."""
    results = [_make_task_result(attempts=[True, True, True, True, True])]
    pass1, pass5 = compute_pass1_pass5(results)
    assert pass1 == pytest.approx(1.0)
    assert pass5 == pytest.approx(1.0)


@pytest.mark.benchmark_micro
def test_compute_pass1_pass5_all_failure() -> None:
    """All attempts failed → pass1=0.0, pass5=0.0."""
    results = [_make_task_result(attempts=[False, False, False, False, False])]
    pass1, pass5 = compute_pass1_pass5(results)
    assert pass1 == pytest.approx(0.0)
    assert pass5 == pytest.approx(0.0)


@pytest.mark.benchmark_micro
def test_compute_pass1_mixed_attempts() -> None:
    """4/5 attempts succeeded → pass1=0.8, pass5=0.0."""
    results = [_make_task_result(attempts=[True, True, False, True, True])]
    pass1, pass5 = compute_pass1_pass5(results)
    assert pass1 == pytest.approx(0.8)
    assert pass5 == pytest.approx(0.0)


@pytest.mark.benchmark_micro
def test_compute_pass1_multiple_tasks() -> None:
    """Pass¹ is the mean across all tasks."""
    # task A: 5/5 = 1.0; task B: 0/5 = 0.0 → mean = 0.5
    r_a = _make_task_result("ta", attempts=[True] * 5)
    r_b = _make_task_result("tb", attempts=[False] * 5)
    pass1, _ = compute_pass1_pass5([r_a, r_b])
    assert pass1 == pytest.approx(0.5)


@pytest.mark.benchmark_micro
def test_compute_pass5_partial_success() -> None:
    """Pass⁵ only counts tasks with ALL 5 attempts succeeded."""
    # 2 tasks: one all-success, one mixed → pass5 = 0.5
    r_all = _make_task_result("ta", attempts=[True] * 5)
    r_mixed = _make_task_result("tb", attempts=[True, True, False, True, True])
    _, pass5 = compute_pass1_pass5([r_all, r_mixed])
    assert pass5 == pytest.approx(0.5)


@pytest.mark.benchmark_micro
def test_compute_pass1_pass5_empty_list() -> None:
    """Empty task list returns (0.0, 0.0) without raising."""
    pass1, pass5 = compute_pass1_pass5([])
    assert pass1 == 0.0
    assert pass5 == 0.0


# ---------------------------------------------------------------------------
# build_task_suite — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_task_suite_has_12_tasks() -> None:
    """build_task_suite() returns exactly 12 tasks."""
    tasks = build_task_suite()
    assert len(tasks) == 12


@pytest.mark.benchmark_micro
def test_task_suite_ids_unique() -> None:
    """All task_ids are unique."""
    tasks = build_task_suite()
    ids = [t.task_id for t in tasks]
    assert len(ids) == len(set(ids))


@pytest.mark.benchmark_micro
def test_task_suite_covers_all_categories() -> None:
    """Task suite covers all four AGENT_QUALITY_CATEGORIES."""
    tasks = build_task_suite()
    covered = {t.category for t in tasks}
    for cat in AGENT_QUALITY_CATEGORIES:
        assert cat in covered, f"Category {cat!r} not covered by any task"


@pytest.mark.benchmark_micro
def test_task_suite_three_per_category() -> None:
    """Exactly 3 tasks per category."""
    tasks = build_task_suite()
    from collections import Counter

    counts = Counter(t.category for t in tasks)
    for cat in AGENT_QUALITY_CATEGORIES:
        assert counts[cat] == 3, f"Category {cat!r} has {counts[cat]} tasks, expected 3"


@pytest.mark.benchmark_micro
def test_task_suite_required_fields_present() -> None:
    """Every task has non-empty task_id, category, sessions, action_turn, gold_action."""
    for task in build_task_suite():
        assert task.task_id, f"task_id missing for {task}"
        assert task.category in AGENT_QUALITY_CATEGORIES, (
            f"Unknown category {task.category!r} in task {task.task_id}"
        )
        assert task.sessions, f"No sessions for task {task.task_id}"
        assert task.action_turn, f"No action_turn for task {task.task_id}"
        assert task.required_memory, f"No required_memory for task {task.task_id}"
        assert task.gold_action, f"No gold_action for task {task.task_id}"


@pytest.mark.benchmark_micro
def test_task_suite_seed_stable() -> None:
    """Same seed produces same task seeds across two calls."""
    tasks_a = build_task_suite(seed=99)
    tasks_b = build_task_suite(seed=99)
    assert [t.seed for t in tasks_a] == [t.seed for t in tasks_b]


@pytest.mark.benchmark_micro
def test_task_suite_different_seeds_produce_different_task_seeds() -> None:
    """Different global seeds produce different task seeds."""
    tasks_42 = build_task_suite(seed=42)
    tasks_99 = build_task_suite(seed=99)
    # At least some task seeds should differ.
    seeds_42 = [t.seed for t in tasks_42]
    seeds_99 = [t.seed for t in tasks_99]
    assert seeds_42 != seeds_99


@pytest.mark.benchmark_micro
def test_task_suite_abstention_tasks_t10_t12() -> None:
    """Tasks t10, t11, t12 are in the abstention category."""
    tasks = {t.task_id: t for t in build_task_suite()}
    for tid in ("t10", "t11", "t12"):
        assert tasks[tid].category == "abstention", (
            f"Task {tid} should be 'abstention', got {tasks[tid].category!r}"
        )


@pytest.mark.benchmark_micro
def test_task_suite_knowledge_update_tasks_t01_t03() -> None:
    """Tasks t01, t02, t03 are in the knowledge-update category."""
    tasks = {t.task_id: t for t in build_task_suite()}
    for tid in ("t01", "t02", "t03"):
        assert tasks[tid].category == "knowledge-update"


# ---------------------------------------------------------------------------
# _build_flat_context — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_flat_context_includes_all_sessions() -> None:
    """_build_flat_context includes turns from every session."""
    task = _make_task(
        sessions=[
            ["User: session 1 turn A.", "Agent: session 1 turn B."],
            ["User: session 2 turn A."],
        ]
    )
    ctx = _build_flat_context(task)
    assert "session 1 turn A" in ctx
    assert "session 1 turn B" in ctx
    assert "session 2 turn A" in ctx


@pytest.mark.benchmark_micro
def test_flat_context_session_labels() -> None:
    """_build_flat_context includes [Session N] headers."""
    task = _make_task(sessions=[["Turn A."], ["Turn B."]])
    ctx = _build_flat_context(task)
    assert "[Session 1]" in ctx
    assert "[Session 2]" in ctx


@pytest.mark.benchmark_micro
def test_flat_context_empty_sessions() -> None:
    """_build_flat_context returns empty string for a task with no sessions."""
    task = _make_task(sessions=[])
    ctx = _build_flat_context(task)
    assert ctx == ""


# ---------------------------------------------------------------------------
# _simulate_agent_response — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_simulate_response_no_memory_non_abstention() -> None:
    """no_memory condition returns a clarifying question for non-abstention tasks."""
    task = _make_task(category="knowledge-update")
    response = _simulate_agent_response(task, "no_memory", "")
    # Should NOT contain the gold action (no memory → can't act).
    assert task.gold_action not in response
    # Should ask for information.
    assert "?" in response or "could you" in response.lower() or "please" in response.lower()


@pytest.mark.benchmark_micro
def test_simulate_response_no_memory_abstention() -> None:
    """no_memory condition for abstention tasks returns the gold action (correct refusal)."""
    task = _make_task(category="abstention", gold_action="I don't have that on file.")
    response = _simulate_agent_response(task, "no_memory", "")
    assert response == task.gold_action


@pytest.mark.benchmark_micro
def test_simulate_response_flat_context_returns_gold() -> None:
    """flat_context with non-empty context returns the gold action."""
    task = _make_task(gold_action="I'll do X as requested.")
    ctx = "User: The thing is X.\nAgent: Noted, X."
    response = _simulate_agent_response(task, "flat_context", ctx)
    assert response == task.gold_action


@pytest.mark.benchmark_micro
def test_simulate_response_with_sdk_returns_gold() -> None:
    """with_sdk with non-empty context returns the gold action."""
    task = _make_task(gold_action="Running X now.")
    response = _simulate_agent_response(task, "with_sdk", "some retrieved context")
    assert response == task.gold_action


# ---------------------------------------------------------------------------
# TaskResult.to_dict — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_task_result_to_dict_shape() -> None:
    """TaskResult.to_dict() emits all required UNI-3 keys."""
    tr = _make_task_result("t01", attempts=[True, True, False, True, True])
    d = tr.to_dict()
    assert "task_id" in d
    assert "category" in d
    assert "condition" in d
    assert "attempts" in d
    assert "pass1" in d
    assert "pass5" in d


@pytest.mark.benchmark_micro
def test_task_result_to_dict_values() -> None:
    """TaskResult.to_dict() values match the constructor arguments."""
    tr = _make_task_result(
        "t03",
        category="multi-session",
        condition="flat_context",
        attempts=[True, False, True, True, True],
    )
    d = tr.to_dict()
    assert d["task_id"] == "t03"
    assert d["category"] == "multi-session"
    assert d["condition"] == "flat_context"
    assert d["attempts"] == [True, False, True, True, True]
    assert d["pass1"] == pytest.approx(0.8)
    assert d["pass5"] is False


@pytest.mark.benchmark_micro
def test_task_result_pass5_true_when_all_succeed() -> None:
    """pass5 is True when all 5 attempts succeed."""
    tr = _make_task_result(attempts=[True, True, True, True, True])
    assert tr.pass5 is True
    assert tr.to_dict()["pass5"] is True


# ---------------------------------------------------------------------------
# AgentQualityResult.to_dict — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_agent_quality_result_to_dict_shape() -> None:
    """AgentQualityResult.to_dict() emits pass1_rate, pass5_rate, per_task."""
    result = AgentQualityResult(
        run_id="abc123",
        condition="flat_context",
        judge_model="llama3.1:8b",
        seed=42,
        per_task=[_make_task_result()],
        pass1_rate=0.8,
        pass5_rate=0.0,
        per_category={"knowledge-update": 0.8},
    )
    d = result.to_dict()
    assert "pass1_rate" in d
    assert "pass5_rate" in d
    assert "per_task" in d
    assert "run_id" in d
    assert "condition" in d
    assert "per_category" in d


@pytest.mark.benchmark_micro
def test_agent_quality_result_to_dict_per_task_list() -> None:
    """per_task in to_dict() is a list of dicts with correct keys."""
    result = AgentQualityResult(
        run_id="x",
        condition="flat_context",
        judge_model="llama3.1:8b",
        seed=42,
        per_task=[_make_task_result("t01"), _make_task_result("t02")],
        pass1_rate=0.8,
        pass5_rate=0.5,
    )
    d = result.to_dict()
    assert len(d["per_task"]) == 2
    for item in d["per_task"]:
        assert "task_id" in item
        assert "pass1" in item
        assert "pass5" in item


# ---------------------------------------------------------------------------
# _compute_per_category — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_compute_per_category_correct() -> None:
    """Per-category pass1 is the mean of per-task pass1 in that category."""
    results = [
        _make_task_result("t01", category="knowledge-update", attempts=[True] * 5),
        _make_task_result("t02", category="knowledge-update", attempts=[False] * 5),
        _make_task_result("t04", category="multi-session", attempts=[True] * 5),
    ]
    per_cat = _compute_per_category(results)
    assert per_cat["knowledge-update"] == pytest.approx(0.5)
    assert per_cat["multi-session"] == pytest.approx(1.0)
    # Categories with no tasks get 0.0.
    assert per_cat["abstention"] == pytest.approx(0.0)
    assert per_cat["temporal-reasoning"] == pytest.approx(0.0)


@pytest.mark.benchmark_micro
def test_compute_per_category_all_categories_present() -> None:
    """_compute_per_category always returns all four category keys."""
    per_cat = _compute_per_category([])
    for cat in AGENT_QUALITY_CATEGORIES:
        assert cat in per_cat


# ---------------------------------------------------------------------------
# OllamaAgentJudge — benchmark_micro (mocked Ollama)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_ollama_judge_returns_true_on_success() -> None:
    """OllamaAgentJudge.judge() returns (True, raw) when model says SUCCESS."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "SUCCESS"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaAgentJudge(model="llama3.1:8b", seed=42)
        success, raw = judge.judge(
            task_description="Deploy to staging.",
            required_memory="Current env: staging.",
            agent_response="Deploying to staging now.",
        )

    assert success is True
    assert raw == "SUCCESS"
    mock_ollama.generate.assert_called_once()


@pytest.mark.benchmark_micro
def test_ollama_judge_returns_false_on_failure() -> None:
    """OllamaAgentJudge.judge() returns (False, raw) when model says FAILURE."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "FAILURE"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaAgentJudge(model="llama3.1:8b", seed=42)
        success, raw = judge.judge(
            task_description="Deploy to staging.",
            required_memory="Current env: staging.",
            agent_response="Deploying to production.",
        )

    assert success is False
    assert raw == "FAILURE"


@pytest.mark.benchmark_micro
def test_ollama_judge_uses_custom_host() -> None:
    """OllamaAgentJudge uses ollama.Client when host is provided."""
    mock_ollama = MagicMock()
    mock_client = MagicMock()
    mock_client.generate.return_value = {"response": "SUCCESS"}
    mock_ollama.Client.return_value = mock_client

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaAgentJudge(
            model="llama3.1:8b", host="http://custom:11434", seed=42
        )
        success, _ = judge.judge(
            task_description="Task.",
            required_memory="Memory.",
            agent_response="Response.",
        )

    assert success is True
    mock_ollama.Client.assert_called_once_with(host="http://custom:11434")
    mock_client.generate.assert_called_once()


@pytest.mark.benchmark_micro
def test_ollama_judge_uses_attempt_seed() -> None:
    """OllamaAgentJudge passes attempt_seed to the generate options."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "SUCCESS"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaAgentJudge(model="llama3.1:8b", seed=42)
        judge.judge(
            task_description="Task.",
            required_memory="Memory.",
            agent_response="Response.",
            attempt_seed=9999,
        )

    call_kwargs = mock_ollama.generate.call_args
    # options dict should contain the attempt_seed value.
    options = call_kwargs.kwargs.get("options") or call_kwargs.args[2] if call_kwargs.args else {}
    if not options:
        # Depending on how the mock was called (positional vs keyword).
        _, call_kw = call_kwargs
        options = call_kw.get("options", {})
    assert options.get("seed") == 9999


@pytest.mark.benchmark_micro
def test_ollama_judge_import_error() -> None:
    """OllamaAgentJudge raises ImportError when ollama is not installed."""
    with patch.dict("sys.modules", {"ollama": None}), pytest.raises(ImportError, match="ollama"):  # type: ignore[dict-item]
        OllamaAgentJudge(model="llama3.1:8b")


# ---------------------------------------------------------------------------
# run_agent_quality_suite — flat_context condition (mocked judge)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_run_suite_flat_context_all_success() -> None:
    """flat_context suite with always-SUCCESS judge produces pass1=1.0, pass5=1.0."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "SUCCESS"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaAgentJudge(model="llama3.1:8b", seed=42)
        result = run_agent_quality_suite(
            condition="flat_context",
            judge=judge,
            seed=42,
            n_attempts=N_ATTEMPTS,
        )

    assert result.pass1_rate == pytest.approx(1.0)
    assert result.pass5_rate == pytest.approx(1.0)
    assert len(result.per_task) == 12


@pytest.mark.benchmark_micro
def test_run_suite_flat_context_all_failure() -> None:
    """flat_context suite with always-FAILURE judge produces pass1=0.0, pass5=0.0."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "FAILURE"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaAgentJudge(model="llama3.1:8b", seed=42)
        result = run_agent_quality_suite(
            condition="flat_context",
            judge=judge,
            seed=42,
            n_attempts=N_ATTEMPTS,
        )

    assert result.pass1_rate == pytest.approx(0.0)
    assert result.pass5_rate == pytest.approx(0.0)


@pytest.mark.benchmark_micro
def test_run_suite_result_has_12_task_results() -> None:
    """Suite result has exactly one TaskResult per task (12 total)."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "SUCCESS"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaAgentJudge(model="llama3.1:8b", seed=42)
        result = run_agent_quality_suite(
            condition="flat_context",
            judge=judge,
            seed=42,
            n_attempts=N_ATTEMPTS,
        )

    assert len(result.per_task) == 12
    task_ids = {tr.task_id for tr in result.per_task}
    expected_ids = {f"t{i:02d}" for i in range(1, 13)}
    assert task_ids == expected_ids


@pytest.mark.benchmark_micro
def test_run_suite_each_task_has_n_attempts() -> None:
    """Each TaskResult.attempts list has exactly N_ATTEMPTS entries."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "SUCCESS"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaAgentJudge(model="llama3.1:8b", seed=42)
        result = run_agent_quality_suite(
            condition="flat_context",
            judge=judge,
            seed=42,
            n_attempts=N_ATTEMPTS,
        )

    for tr in result.per_task:
        assert len(tr.attempts) == N_ATTEMPTS, (
            f"Task {tr.task_id} has {len(tr.attempts)} attempts, expected {N_ATTEMPTS}"
        )


@pytest.mark.benchmark_micro
def test_run_suite_condition_stamped_on_results() -> None:
    """Each TaskResult carries the correct condition value."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "SUCCESS"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaAgentJudge(model="llama3.1:8b", seed=42)
        result = run_agent_quality_suite(
            condition="flat_context",
            judge=judge,
            seed=42,
            n_attempts=2,
        )

    for tr in result.per_task:
        assert tr.condition == "flat_context"


@pytest.mark.benchmark_micro
def test_run_suite_per_category_keys_present() -> None:
    """Suite result has per_category entry for all four categories."""
    mock_ollama = MagicMock()
    mock_ollama.generate.return_value = {"response": "SUCCESS"}

    with patch.dict("sys.modules", {"ollama": mock_ollama}):
        judge = OllamaAgentJudge(model="llama3.1:8b", seed=42)
        result = run_agent_quality_suite(
            condition="flat_context",
            judge=judge,
            seed=42,
            n_attempts=2,
        )

    for cat in AGENT_QUALITY_CATEGORIES:
        assert cat in result.per_category, f"Category {cat!r} missing from per_category"


# ---------------------------------------------------------------------------
# run_agent_quality_suite — no_memory condition (mocked judge)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_run_suite_no_memory_judge_called_correctly() -> None:
    """no_memory condition calls the judge with the simulated agent response."""
    judge_calls: list[dict] = []

    def _recording_judge(**kwargs: Any) -> tuple[bool, str]:
        judge_calls.append(dict(kwargs))
        return True, "SUCCESS"

    # Monkey-patch the judge object.
    mock_judge = MagicMock(spec=OllamaAgentJudge)
    mock_judge.model = "llama3.1:8b"
    mock_judge.judge.side_effect = _recording_judge

    result = run_agent_quality_suite(
        condition="no_memory",
        judge=mock_judge,
        seed=42,
        n_attempts=1,
    )

    # Judge was called once per task (12 tasks × 1 attempt).
    assert mock_judge.judge.call_count == 12
    assert len(result.per_task) == 12


@pytest.mark.benchmark_micro
def test_run_suite_no_memory_condition_stamped() -> None:
    """no_memory condition is stamped on every TaskResult."""
    mock_judge = MagicMock(spec=OllamaAgentJudge)
    mock_judge.model = "llama3.1:8b"
    mock_judge.judge.return_value = (True, "SUCCESS")

    result = run_agent_quality_suite(
        condition="no_memory",
        judge=mock_judge,
        seed=42,
        n_attempts=1,
    )

    for tr in result.per_task:
        assert tr.condition == "no_memory"


# ---------------------------------------------------------------------------
# run_agent_quality_suite — with_sdk condition (mocked store + judge)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_run_suite_with_sdk_calls_store_remember() -> None:
    """with_sdk condition calls store.remember() for every session turn."""
    mock_store = MagicMock()
    mock_store.working.search.return_value = []

    mock_judge = MagicMock(spec=OllamaAgentJudge)
    mock_judge.model = "llama3.1:8b"
    mock_judge.judge.return_value = (True, "SUCCESS")

    mock_embedding = MagicMock(return_value=[0.1] * 5)

    result = run_agent_quality_suite(
        condition="with_sdk",
        judge=mock_judge,
        seed=42,
        n_attempts=1,
        store=mock_store,
        embedding_provider=mock_embedding,
    )

    # store.remember() must have been called at least once.
    assert mock_store.remember.call_count > 0
    assert len(result.per_task) == 12


@pytest.mark.benchmark_micro
def test_run_suite_with_sdk_calls_search() -> None:
    """with_sdk condition calls store.working.search() for every task attempt."""
    mock_store = MagicMock()
    mock_store.working.search.return_value = []

    mock_judge = MagicMock(spec=OllamaAgentJudge)
    mock_judge.model = "llama3.1:8b"
    mock_judge.judge.return_value = (True, "SUCCESS")

    mock_embedding = MagicMock(return_value=[0.1] * 5)

    run_agent_quality_suite(
        condition="with_sdk",
        judge=mock_judge,
        seed=42,
        n_attempts=1,
        store=mock_store,
        embedding_provider=mock_embedding,
    )

    # search() called once per task per attempt (12 tasks × 1 attempt = 12 calls).
    assert mock_store.working.search.call_count == 12


@pytest.mark.benchmark_micro
def test_run_suite_with_sdk_calls_erase_all() -> None:
    """with_sdk condition calls store.erase_all() after each attempt."""
    mock_store = MagicMock()
    mock_store.working.search.return_value = []

    mock_judge = MagicMock(spec=OllamaAgentJudge)
    mock_judge.model = "llama3.1:8b"
    mock_judge.judge.return_value = (True, "SUCCESS")

    mock_embedding = MagicMock(return_value=[0.1] * 5)

    run_agent_quality_suite(
        condition="with_sdk",
        judge=mock_judge,
        seed=42,
        n_attempts=1,
        store=mock_store,
        embedding_provider=mock_embedding,
    )

    # erase_all() called once per task per attempt (12 × 1 = 12 calls).
    assert mock_store.erase_all.call_count == 12


@pytest.mark.benchmark_micro
def test_run_suite_with_sdk_embedding_provider_called() -> None:
    """with_sdk condition calls the embedding provider with action_turn per attempt."""
    mock_store = MagicMock()
    mock_store.working.search.return_value = []

    mock_judge = MagicMock(spec=OllamaAgentJudge)
    mock_judge.model = "llama3.1:8b"
    mock_judge.judge.return_value = (True, "SUCCESS")

    call_texts: list[str] = []

    def _recording_embed(text: str) -> list[float]:
        call_texts.append(text)
        return [0.1] * 5

    run_agent_quality_suite(
        condition="with_sdk",
        judge=mock_judge,
        seed=42,
        n_attempts=1,
        store=mock_store,
        embedding_provider=_recording_embed,
    )

    # Called once per task per attempt = 12 calls.
    assert len(call_texts) == 12
    # Each call should be the task's action_turn text.
    tasks = build_task_suite()
    expected_texts = [t.action_turn for t in tasks]
    assert call_texts == expected_texts


# ---------------------------------------------------------------------------
# run_agent_quality_suite — error handling
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_run_suite_bad_condition_raises() -> None:
    """run_agent_quality_suite raises ValueError for an unknown condition."""
    mock_judge = MagicMock(spec=OllamaAgentJudge)
    mock_judge.model = "llama3.1:8b"

    with pytest.raises(ValueError, match="Unknown condition"):
        run_agent_quality_suite(
            condition="invalid_condition",
            judge=mock_judge,
        )


@pytest.mark.benchmark_micro
def test_run_suite_with_sdk_missing_store_raises() -> None:
    """run_agent_quality_suite raises ValueError when store= is omitted for with_sdk."""
    mock_judge = MagicMock(spec=OllamaAgentJudge)
    mock_judge.model = "llama3.1:8b"

    with pytest.raises(ValueError, match="store="):
        run_agent_quality_suite(
            condition="with_sdk",
            judge=mock_judge,
            embedding_provider=MagicMock(),
        )


@pytest.mark.benchmark_micro
def test_run_suite_with_sdk_missing_embedding_provider_raises() -> None:
    """run_agent_quality_suite raises ValueError when embedding_provider= is omitted for with_sdk."""
    mock_judge = MagicMock(spec=OllamaAgentJudge)
    mock_judge.model = "llama3.1:8b"

    with pytest.raises(ValueError, match="embedding_provider="):
        run_agent_quality_suite(
            condition="with_sdk",
            judge=mock_judge,
            store=MagicMock(),
        )


# ---------------------------------------------------------------------------
# Condition constants — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_conditions_constant_has_three_values() -> None:
    """CONDITIONS tuple contains exactly the three expected values."""
    assert len(CONDITIONS) == 3
    assert "with_sdk" in CONDITIONS
    assert "flat_context" in CONDITIONS
    assert "no_memory" in CONDITIONS


@pytest.mark.benchmark_micro
def test_n_attempts_is_five() -> None:
    """N_ATTEMPTS is 5 (Pass¹/Pass⁵ definition)."""
    assert N_ATTEMPTS == 5


# ---------------------------------------------------------------------------
# Pass¹ / Pass⁵ edge cases — benchmark_micro
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_micro
def test_pass1_rate_in_0_1() -> None:
    """Pass¹ rate is always in [0, 1]."""
    for attempts in [
        [True] * 5,
        [False] * 5,
        [True, False, True, False, True],
    ]:
        results = [_make_task_result(attempts=attempts)]
        pass1, _ = compute_pass1_pass5(results)
        assert 0.0 <= pass1 <= 1.0


@pytest.mark.benchmark_micro
def test_pass5_rate_in_0_1() -> None:
    """Pass⁵ rate is always in [0, 1]."""
    results = [
        _make_task_result("ta", attempts=[True] * 5),
        _make_task_result("tb", attempts=[True, False, True, True, True]),
    ]
    _, pass5 = compute_pass1_pass5(results)
    assert 0.0 <= pass5 <= 1.0


@pytest.mark.benchmark_micro
def test_run_suite_n_attempts_respected() -> None:
    """n_attempts parameter is respected (3 instead of default 5)."""
    mock_judge = MagicMock(spec=OllamaAgentJudge)
    mock_judge.model = "llama3.1:8b"
    mock_judge.judge.return_value = (True, "SUCCESS")

    result = run_agent_quality_suite(
        condition="no_memory",
        judge=mock_judge,
        seed=42,
        n_attempts=3,
    )

    for tr in result.per_task:
        assert len(tr.attempts) == 3, (
            f"Task {tr.task_id} has {len(tr.attempts)} attempts, expected 3"
        )
    # 12 tasks × 3 attempts = 36 judge calls.
    assert mock_judge.judge.call_count == 36

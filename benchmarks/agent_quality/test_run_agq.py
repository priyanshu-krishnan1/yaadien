"""
benchmarks/agent_quality/test_run_agq.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
AGQ-5 (EPIC-21): Unit tests for run_agq.py — the AGQ-2/3/4 merge helper.

All tests are marked ``benchmark_micro`` so they run in the standard test suite
without a live Db2 instance or LLM judge.
"""

from __future__ import annotations

import json

import pytest

from benchmarks.agent_quality.run_agq import merge_agq_results

# ---------------------------------------------------------------------------
# merge_agq_results
# ---------------------------------------------------------------------------


class TestMergeAgqResults:
    """Unit tests for merge_agq_results()."""

    def test_all_three_inputs_present(self) -> None:
        agq2 = {"pass1_rate": 0.75, "pass5_rate": 0.60, "judge_model": "llama3.1:8b", "seed": 42}
        agq3 = {"groundedness_mean": 3.8, "seed": 42}
        agq4 = {"coherence_mean": 4.0, "fluency_mean": 4.1}

        result = merge_agq_results(agq2, agq3, agq4)

        assert result["pass1_rate"] == 0.75
        assert result["pass5_rate"] == 0.60
        assert result["groundedness_mean"] == 3.8
        assert result["coherence_mean"] == 4.0
        assert result["fluency_mean"] == 4.1
        assert result["judge_model"] == "llama3.1:8b"
        assert result["seed"] == 42

    def test_missing_agq2_omits_pass_rates(self) -> None:
        agq3 = {"groundedness_mean": 3.8}
        agq4 = {"coherence_mean": 4.0, "fluency_mean": 4.1}

        result = merge_agq_results(None, agq3, agq4)

        assert "pass1_rate" not in result
        assert "pass5_rate" not in result
        assert result["groundedness_mean"] == 3.8
        assert result["coherence_mean"] == 4.0
        assert result["fluency_mean"] == 4.1

    def test_missing_agq3_omits_groundedness(self) -> None:
        agq2 = {"pass1_rate": 0.75, "pass5_rate": 0.60}
        agq4 = {"coherence_mean": 4.0, "fluency_mean": 4.1}

        result = merge_agq_results(agq2, None, agq4)

        assert result["pass1_rate"] == 0.75
        assert "groundedness_mean" not in result
        assert result["coherence_mean"] == 4.0

    def test_missing_agq4_omits_coherence_fluency(self) -> None:
        agq2 = {"pass1_rate": 0.8, "pass5_rate": 0.6}
        agq3 = {"groundedness_mean": 3.5}

        result = merge_agq_results(agq2, agq3, None)

        assert result["pass1_rate"] == 0.8
        assert result["groundedness_mean"] == 3.5
        assert "coherence_mean" not in result
        assert "fluency_mean" not in result

    def test_all_none_returns_empty_dict(self) -> None:
        result = merge_agq_results(None, None, None)
        assert result == {}

    def test_judge_model_taken_from_first_available(self) -> None:
        # AGQ-2 has model, agq3 also has model — AGQ-2 wins (first seen).
        agq2 = {"pass1_rate": 0.75, "judge_model": "llama3.1:8b"}
        agq3 = {"groundedness_mean": 3.8, "judge_model": "deepseek-r1:8b"}

        result = merge_agq_results(agq2, agq3, None)

        assert result["judge_model"] == "llama3.1:8b"

    def test_judge_model_from_agq3_when_agq2_missing(self) -> None:
        agq3 = {"groundedness_mean": 3.8, "judge_model": "deepseek-r1:8b"}

        result = merge_agq_results(None, agq3, None)

        assert result["judge_model"] == "deepseek-r1:8b"

    def test_scorecard_required_keys_present_when_all_inputs_given(self) -> None:
        """Validate that the merged output has the four keys scorecard._compute_agent_quality expects."""
        agq2 = {"pass1_rate": 0.75, "pass5_rate": 0.60, "judge_model": "llama3.1:8b", "seed": 42}
        agq3 = {"groundedness_mean": 3.8, "seed": 42}
        agq4 = {"coherence_mean": 4.0, "fluency_mean": 4.1}

        result = merge_agq_results(agq2, agq3, agq4)

        required = {"pass1_rate", "groundedness_mean", "coherence_mean", "fluency_mean"}
        assert required.issubset(set(result)), (
            f"Missing required scorecard keys: {required - set(result)}"
        )

    def test_pass5_supplementary_only(self) -> None:
        """pass5_rate is included when present but is supplementary (not in score formula)."""
        agq2 = {"pass1_rate": 0.80, "pass5_rate": 0.60}
        result = merge_agq_results(agq2, None, None)
        assert result["pass5_rate"] == 0.60

    @pytest.mark.benchmark_micro
    def test_json_serialisable(self) -> None:
        """Output must be JSON-serialisable (no non-primitive types)."""
        agq2 = {"pass1_rate": 0.75, "pass5_rate": 0.60, "judge_model": "m", "seed": 42}
        agq3 = {"groundedness_mean": 3.8}
        agq4 = {"coherence_mean": 4.0, "fluency_mean": 4.1}

        result = merge_agq_results(agq2, agq3, agq4)
        # Must not raise
        json.dumps(result)

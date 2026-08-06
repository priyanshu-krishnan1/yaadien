"""
tests/test_scorecard.py
~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for ``benchmarks/common/scorecard.py``.

All tests are fixture-based (no live Db2, no LLM judge) — per UNI-3's
acceptance criteria:

    "scorecard.py produces a deterministic Markdown table given fixed input
    JSON fixtures (unit-testable without a live Db2 instance or LLM judge)."

Test categories
---------------
- Normalization helpers (_normalize_op_score, _likert_to_0_100)
- Weight loading and validation (_load_weights)
- Per-sub-score computation (_compute_performance, _compute_retrieval,
  _compute_agent_quality)
- Top-level compute_scorecard() — full, partial, and missing scenarios
- render_markdown() — structural correctness, MISSING surfacing,
  config-driven weight reflection
"""

from __future__ import annotations

from pathlib import Path

import pytest
from benchmarks.common.scorecard import (
    ScorecardResult,
    _compute_agent_quality,
    _compute_performance,
    _compute_retrieval,
    _likert_to_0_100,
    _load_weights,
    _normalize_op_score,
    compute_scorecard,
    render_markdown,
)

# ---------------------------------------------------------------------------
# Fixtures — reusable test data
# ---------------------------------------------------------------------------

SAMPLE_WEIGHTS = {
    "performance": 0.333,
    "retrieval_accuracy": 0.333,
    "agent_quality": 0.334,
}

SAMPLE_BASELINES: dict = {
    "test_remember_latency": 0.01,   # 10 ms baseline
    "test_search_latency": 0.02,     # 20 ms baseline
}

SAMPLE_PERF_JSON: dict = {
    "benchmarks": [
        {
            "fullname": "benchmarks/latency/test_remember_latency",
            "stats": {"mean": 0.009},  # 0.9× baseline → 100
        },
        {
            "fullname": "benchmarks/latency/test_search_latency",
            "stats": {"mean": 0.030},  # 1.5× baseline → 75
        },
    ]
}

SAMPLE_BM17: dict = {"recall_at_k": 0.90}         # 90 → 90.0
SAMPLE_BM18: dict = {"accuracy": 88.0}            # already 0-100
SAMPLE_AGQ: dict = {
    "pass1_rate": 0.80,          # ×100 → 80.0
    "pass5_rate": 0.95,          # supplementary
    "groundedness_mean": 4.0,    # ×20 → 80.0
    "coherence_mean": 3.5,       # ×20 → 70.0
    "fluency_mean": 4.5,         # ×20 → 90.0
}


@pytest.fixture
def weights_yaml(tmp_path: Path) -> Path:
    f = tmp_path / "scoring_weights.yaml"
    f.write_text(
        "weights:\n"
        "  performance: 0.333\n"
        "  retrieval_accuracy: 0.333\n"
        "  agent_quality: 0.334\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture
def alt_weights_yaml(tmp_path: Path) -> Path:
    """Alternate weights — performance heavily weighted."""
    f = tmp_path / "scoring_weights_alt.yaml"
    f.write_text(
        "weights:\n"
        "  performance: 0.500\n"
        "  retrieval_accuracy: 0.250\n"
        "  agent_quality: 0.250\n",
        encoding="utf-8",
    )
    return f


# ---------------------------------------------------------------------------
# _normalize_op_score
# ---------------------------------------------------------------------------


class TestNormalizeOpScore:
    def test_at_or_better_than_baseline(self):
        assert _normalize_op_score(1.0) == 100.0
        assert _normalize_op_score(0.5) == 100.0
        assert _normalize_op_score(0.0) == 100.0

    def test_at_alert_threshold(self):
        assert _normalize_op_score(1.5) == pytest.approx(75.0)

    def test_at_fail_threshold(self):
        assert _normalize_op_score(3.0) == pytest.approx(0.0)
        assert _normalize_op_score(5.0) == pytest.approx(0.0)

    def test_between_baseline_and_alert(self):
        # pct=1.25 → midpoint of [1.0→100, 1.5→75] → 87.5
        assert _normalize_op_score(1.25) == pytest.approx(87.5)

    def test_between_alert_and_fail(self):
        # pct=2.25 → midpoint of [1.5→75, 3.0→0] → 37.5
        assert _normalize_op_score(2.25) == pytest.approx(37.5)

    def test_result_bounded_0_to_100(self):
        for pct in [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 10.0]:
            score = _normalize_op_score(pct)
            assert 0.0 <= score <= 100.0


# ---------------------------------------------------------------------------
# _likert_to_0_100
# ---------------------------------------------------------------------------


class TestLikertNormalization:
    def test_min_value(self):
        assert _likert_to_0_100(1.0) == pytest.approx(20.0)

    def test_mid_value(self):
        assert _likert_to_0_100(3.0) == pytest.approx(60.0)

    def test_max_value(self):
        assert _likert_to_0_100(5.0) == pytest.approx(100.0)

    def test_fractional(self):
        assert _likert_to_0_100(3.5) == pytest.approx(70.0)


# ---------------------------------------------------------------------------
# _load_weights
# ---------------------------------------------------------------------------


class TestLoadWeights:
    def test_valid_weights_loaded(self, weights_yaml: Path):
        w = _load_weights(weights_yaml)
        assert w["performance"] == pytest.approx(0.333)
        assert w["retrieval_accuracy"] == pytest.approx(0.333)
        assert w["agent_quality"] == pytest.approx(0.334)

    def test_sum_must_be_1(self, tmp_path: Path):
        f = tmp_path / "bad.yaml"
        f.write_text(
            "weights:\n"
            "  performance: 0.5\n"
            "  retrieval_accuracy: 0.5\n"
            "  agent_quality: 0.5\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="sum"):
            _load_weights(f)

    def test_missing_key_raises(self, tmp_path: Path):
        f = tmp_path / "incomplete.yaml"
        f.write_text(
            "weights:\n"
            "  performance: 0.5\n"
            "  retrieval_accuracy: 0.5\n",  # agent_quality missing
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="agent_quality"):
            _load_weights(f)

    def test_file_not_found_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            _load_weights(tmp_path / "nonexistent.yaml")

    def test_tolerance_accepted(self, tmp_path: Path):
        """Floating-point sum of 0.999 is within ±0.001 tolerance."""
        f = tmp_path / "near_1.yaml"
        f.write_text(
            "weights:\n"
            "  performance: 0.333\n"
            "  retrieval_accuracy: 0.333\n"
            "  agent_quality: 0.333\n",  # sum=0.999, within tolerance
            encoding="utf-8",
        )
        w = _load_weights(f)
        assert sum(w.values()) == pytest.approx(0.999)

    def test_alternate_weights_loaded(self, alt_weights_yaml: Path):
        w = _load_weights(alt_weights_yaml)
        assert w["performance"] == pytest.approx(0.5)
        assert sum(w.values()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _compute_performance
# ---------------------------------------------------------------------------


class TestComputePerformance:
    def test_baseline_at_1x_gives_100(self):
        perf_json = {
            "benchmarks": [
                {"fullname": "test_op_a", "stats": {"mean": 0.01}},
            ]
        }
        baselines = {"test_op_a": 0.01}
        ps = _compute_performance(perf_json, baselines)
        assert ps.is_available
        assert ps.score == pytest.approx(100.0)

    def test_alert_threshold_gives_75(self):
        perf_json = {
            "benchmarks": [
                {"fullname": "test_op_b", "stats": {"mean": 0.030}},
            ]
        }
        baselines = {"test_op_b": 0.020}  # 1.5× → 75
        ps = _compute_performance(perf_json, baselines)
        assert ps.is_available
        assert ps.score == pytest.approx(75.0)

    def test_fail_threshold_gives_0(self):
        perf_json = {
            "benchmarks": [
                {"fullname": "test_op_c", "stats": {"mean": 0.06}},
            ]
        }
        baselines = {"test_op_c": 0.02}  # 3× → 0
        ps = _compute_performance(perf_json, baselines)
        assert ps.is_available
        assert ps.score == pytest.approx(0.0)

    def test_missing_baselines_json(self):
        ps = _compute_performance({"benchmarks": []}, None)
        assert not ps.is_available
        assert "baselines" in (ps.missing_reason or "").lower()

    def test_missing_perf_json(self):
        ps = _compute_performance(None, {"test_op": 0.01})
        assert not ps.is_available
        assert "pytest-benchmark" in (ps.missing_reason or "").lower()

    def test_no_matching_ops_gives_missing(self):
        perf_json = {
            "benchmarks": [
                {"fullname": "totally_different_op", "stats": {"mean": 0.01}},
            ]
        }
        baselines = {"test_op_z": 0.01}
        ps = _compute_performance(perf_json, baselines)
        assert not ps.is_available
        assert "no operations matched" in (ps.missing_reason or "").lower()

    def test_per_op_scores_stored(self):
        perf_json = {
            "benchmarks": [
                {"fullname": "test_write_latency", "stats": {"mean": 0.01}},
                {"fullname": "test_read_latency", "stats": {"mean": 0.03}},
            ]
        }
        baselines = {"test_write_latency": 0.01, "test_read_latency": 0.02}
        ps = _compute_performance(perf_json, baselines)
        assert ps.is_available
        assert "test_write_latency" in ps.per_op
        assert "test_read_latency" in ps.per_op

    def test_sample_fixture_produces_correct_mean(self):
        # Op1: 0.009/0.01 = 0.9× → 100; Op2: 0.030/0.020 = 1.5× → 75
        # mean = (100 + 75) / 2 = 87.5
        ps = _compute_performance(SAMPLE_PERF_JSON, SAMPLE_BASELINES)
        assert ps.is_available
        assert ps.score == pytest.approx(87.5)


# ---------------------------------------------------------------------------
# _compute_retrieval
# ---------------------------------------------------------------------------


class TestComputeRetrieval:
    def test_both_available_composite_is_average(self):
        rs = _compute_retrieval(SAMPLE_BM17, SAMPLE_BM18)
        # det = 0.90 × 100 = 90; judged = 88; composite = (90+88)/2 = 89
        assert rs.is_fully_available
        assert rs.composite == pytest.approx(89.0)

    def test_only_deterministic_partial(self):
        rs = _compute_retrieval(SAMPLE_BM17, None)
        assert rs.is_partially_available
        assert not rs.is_fully_available
        assert rs.partial == pytest.approx(90.0)
        assert "nightly only" in (rs.missing_judged_reason or "")

    def test_neither_available(self):
        rs = _compute_retrieval(None, None)
        assert rs.det_score is None
        assert rs.judged_score is None
        assert not rs.is_fully_available
        assert not rs.is_partially_available

    def test_missing_recall_at_k_key(self):
        rs = _compute_retrieval({"wrong_key": 0.9}, SAMPLE_BM18)
        assert rs.det_score is None
        assert "recall_at_k" in (rs.missing_det_reason or "")

    def test_det_score_is_0_to_100(self):
        rs = _compute_retrieval({"recall_at_k": 1.0}, None)
        assert rs.det_score == pytest.approx(100.0)

        rs2 = _compute_retrieval({"recall_at_k": 0.0}, None)
        assert rs2.det_score == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _compute_agent_quality
# ---------------------------------------------------------------------------


class TestComputeAgentQuality:
    def test_full_agq_json_produces_score(self):
        aq = _compute_agent_quality(SAMPLE_AGQ)
        assert aq.is_available
        # pass1: 0.80×100=80; ground: 4.0×20=80; coherence: 3.5×20=70; fluency: 4.5×20=90
        # A-score = (80+80+70+90)/4 = 80.0
        assert aq.score == pytest.approx(80.0)

    def test_pass5_is_supplementary_not_in_formula(self):
        """Removing pass5_rate from the input must not change the score."""
        agq_without_p5 = {k: v for k, v in SAMPLE_AGQ.items() if k != "pass5_rate"}
        aq = _compute_agent_quality(agq_without_p5)
        assert aq.is_available
        assert aq.score == pytest.approx(80.0)

    def test_missing_agq_json_gives_missing(self):
        aq = _compute_agent_quality(None)
        assert not aq.is_available
        assert "AGQ suite" in (aq.missing_reason or "")

    def test_missing_required_key_gives_missing(self):
        partial = {k: v for k, v in SAMPLE_AGQ.items() if k != "pass1_rate"}
        aq = _compute_agent_quality(partial)
        assert not aq.is_available
        assert "pass1_rate" in (aq.missing_reason or "")

    def test_likert_values_are_normalized(self):
        aq = _compute_agent_quality(SAMPLE_AGQ)
        assert aq.groundedness_norm == pytest.approx(80.0)  # 4.0 × 20
        assert aq.coherence_norm == pytest.approx(70.0)      # 3.5 × 20
        assert aq.fluency_norm == pytest.approx(90.0)        # 4.5 × 20


# ---------------------------------------------------------------------------
# compute_scorecard — full / partial / incomplete
# ---------------------------------------------------------------------------


class TestComputeScorecard:
    def test_full_scorecard_all_inputs_available(self):
        sc = compute_scorecard(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=SAMPLE_BM18,
            agq_json=SAMPLE_AGQ,
            weights=SAMPLE_WEIGHTS,
        )
        assert sc.run_mode == "full"
        assert sc.mbs is not None
        # P=87.5, R=(90+88)/2=89, A=80
        # MBS = (0.333×87.5) + (0.333×89) + (0.334×80)
        expected = 0.333 * 87.5 + 0.333 * 89.0 + 0.334 * 80.0
        assert sc.mbs == pytest.approx(expected, rel=1e-4)

    def test_partial_scorecard_no_bm18_no_agq(self):
        sc = compute_scorecard(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=None,
            agq_json=None,
            weights=SAMPLE_WEIGHTS,
        )
        assert sc.run_mode == "partial"
        assert sc.mbs is None  # full composite not available
        assert sc.mbs_partial is not None

    def test_incomplete_scorecard_no_perf(self):
        sc = compute_scorecard(
            perf_json=None,
            baselines_json=None,
            bm17_json=SAMPLE_BM17,
            bm18_json=SAMPLE_BM18,
            agq_json=SAMPLE_AGQ,
            weights=SAMPLE_WEIGHTS,
        )
        assert sc.run_mode == "incomplete"
        assert sc.mbs is None
        assert sc.mbs_partial is None

    def test_changing_weights_changes_mbs(self):
        """Config-driven: different weights → different MBS (UNI-2 requirement)."""
        sc_equal = compute_scorecard(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=SAMPLE_BM18,
            agq_json=SAMPLE_AGQ,
            weights=SAMPLE_WEIGHTS,
        )
        heavy_perf = {"performance": 0.6, "retrieval_accuracy": 0.2, "agent_quality": 0.2}
        sc_heavy = compute_scorecard(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=SAMPLE_BM18,
            agq_json=SAMPLE_AGQ,
            weights=heavy_perf,
        )
        assert sc_equal.mbs != pytest.approx(sc_heavy.mbs)

    def test_deterministic_given_fixed_inputs(self):
        """Same inputs → same MBS on every call."""
        kwargs = dict(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=SAMPLE_BM18,
            agq_json=SAMPLE_AGQ,
            weights=SAMPLE_WEIGHTS,
        )
        sc1 = compute_scorecard(**kwargs)
        sc2 = compute_scorecard(**kwargs)
        assert sc1.mbs == sc2.mbs


# ---------------------------------------------------------------------------
# render_markdown — structural correctness
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def _make_full_sc(self) -> ScorecardResult:
        return compute_scorecard(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=SAMPLE_BM18,
            agq_json=SAMPLE_AGQ,
            weights=SAMPLE_WEIGHTS,
        )

    def _make_partial_sc(self) -> ScorecardResult:
        return compute_scorecard(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=None,
            agq_json=None,
            weights=SAMPLE_WEIGHTS,
        )

    def _make_incomplete_sc(self) -> ScorecardResult:
        return compute_scorecard(
            perf_json=None,
            baselines_json=None,
            bm17_json=None,
            bm18_json=None,
            agq_json=None,
            weights=SAMPLE_WEIGHTS,
        )

    def test_full_render_contains_scorecard_heading(self):
        md = render_markdown(self._make_full_sc())
        assert "## Composite Scorecard" in md

    def test_full_render_contains_mbs_value(self):
        sc = self._make_full_sc()
        md = render_markdown(sc)
        assert sc.mbs is not None
        assert f"{sc.mbs:.1f}" in md

    def test_partial_render_signals_partial(self):
        md = render_markdown(self._make_partial_sc())
        assert "partial" in md.lower() or "Partial" in md

    def test_incomplete_render_says_incomplete(self):
        md = render_markdown(self._make_incomplete_sc())
        assert "INCOMPLETE" in md

    def test_missing_inputs_surfaced_not_zeroed(self):
        """MISSING must appear for absent sub-scores — not 0 or silence."""
        md = render_markdown(self._make_incomplete_sc())
        assert "MISSING" in md
        assert "0.0" not in md.replace("0.0 /", "")  # 0 must not appear as a score

    def test_missing_agq_surfaced(self):
        sc = compute_scorecard(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=SAMPLE_BM18,
            agq_json=None,
            weights=SAMPLE_WEIGHTS,
        )
        md = render_markdown(sc)
        assert "MISSING" in md
        assert "AGQ" in md

    def test_weights_reflected_in_output(self):
        """Changing the weights file changes the composite — must be visible."""
        sc = self._make_full_sc()
        md = render_markdown(sc)
        assert "scoring_weights.yaml" in md

    def test_benchmark_scoring_md_cross_referenced(self):
        md = render_markdown(self._make_full_sc())
        assert "BENCHMARK_SCORING.md" in md

    def test_agent_quality_breakdown_present_when_available(self):
        md = render_markdown(self._make_full_sc())
        assert "Pass¹" in md
        assert "Groundedness" in md
        assert "Coherence" in md
        assert "Fluency" in md

    def test_pass5_labeled_supplementary(self):
        md = render_markdown(self._make_full_sc())
        assert "supplementary" in md.lower()

    def test_per_sub_score_values_correct(self):
        """All three sub-score values must appear in the output."""
        sc = self._make_full_sc()
        md = render_markdown(sc)
        assert f"{sc.perf.score:.1f}" in md
        assert sc.retrieval.composite is not None
        assert f"{sc.retrieval.composite:.1f}" in md
        assert sc.agent_quality.score is not None
        assert f"{sc.agent_quality.score:.1f}" in md

    def test_oracle_mem0_microsoft_labels_present(self):
        md = render_markdown(self._make_full_sc())
        assert "Oracle" in md
        assert "Mem0" in md
        assert "Microsoft" in md

    def test_nightly_only_label_on_bm18(self):
        md = render_markdown(self._make_full_sc())
        assert "nightly" in md.lower() or "Nightly" in md

    def test_render_is_deterministic(self):
        sc = self._make_full_sc()
        assert render_markdown(sc) == render_markdown(sc)


# ---------------------------------------------------------------------------
# Integration — _load_weights + compute_scorecard end-to-end
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_loading_weights_then_computing(self, weights_yaml: Path):
        w = _load_weights(weights_yaml)
        sc = compute_scorecard(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=SAMPLE_BM18,
            agq_json=SAMPLE_AGQ,
            weights=w,
        )
        assert sc.run_mode == "full"
        assert sc.mbs is not None
        assert 0.0 <= sc.mbs <= 100.0

    def test_alternate_weights_produces_different_mbs(
        self, weights_yaml: Path, alt_weights_yaml: Path
    ):
        w_default = _load_weights(weights_yaml)
        w_alt = _load_weights(alt_weights_yaml)

        kwargs = dict(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=SAMPLE_BM18,
            agq_json=SAMPLE_AGQ,
        )
        sc_default = compute_scorecard(**kwargs, weights=w_default)
        sc_alt = compute_scorecard(**kwargs, weights=w_alt)

        # P=87.5, R=89, A=80  →  different weights → different MBS
        assert sc_default.mbs is not None
        assert sc_alt.mbs is not None
        assert sc_default.mbs != pytest.approx(sc_alt.mbs, rel=1e-3)

    def test_rendered_markdown_valid_for_benchmarks_append(self, tmp_path: Path):
        """Rendered output can be cleanly appended to a stub BENCHMARKS.md."""
        from benchmarks.common.scorecard import _append_scorecard_to_benchmarks

        stub = tmp_path / "BENCHMARKS.md"
        stub.write_text(
            "# Benchmark Report\n\n---\n\n## Methodology summary\n\nSome content.\n",
            encoding="utf-8",
        )
        w = _load_weights(
            # write a temp weights file
            (lambda f: (f.write_text("weights:\n  performance: 0.333\n  retrieval_accuracy: 0.333\n  agent_quality: 0.334\n"), f)[1])(tmp_path / "w.yaml")
        )
        sc = compute_scorecard(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=SAMPLE_BM18,
            agq_json=SAMPLE_AGQ,
            weights=w,
        )
        md = render_markdown(sc)
        _append_scorecard_to_benchmarks(stub, md)

        result = stub.read_text(encoding="utf-8")
        assert "## Composite Scorecard" in result
        assert "Some content." in result  # original preserved

    def test_second_append_replaces_not_duplicates(self, tmp_path: Path):
        """Calling append twice does not produce two scorecard sections."""
        from benchmarks.common.scorecard import _append_scorecard_to_benchmarks

        stub = tmp_path / "BENCHMARKS.md"
        stub.write_text("# Benchmark Report\n\n---\n\nBody.\n", encoding="utf-8")
        sc = compute_scorecard(
            perf_json=SAMPLE_PERF_JSON,
            baselines_json=SAMPLE_BASELINES,
            bm17_json=SAMPLE_BM17,
            bm18_json=SAMPLE_BM18,
            agq_json=SAMPLE_AGQ,
            weights=SAMPLE_WEIGHTS,
        )
        md = render_markdown(sc)
        _append_scorecard_to_benchmarks(stub, md)
        _append_scorecard_to_benchmarks(stub, md)

        result = stub.read_text(encoding="utf-8")
        assert result.count("## Composite Scorecard") == 1

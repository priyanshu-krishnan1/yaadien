"""
benchmarks/common/scorecard.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Memory Benchmark Score (MBS) — composite scorecard generator.

Reads the three sub-score sources defined in
``project-management/BENCHMARK_SCORING.md`` (UNI-2), applies normalization and
weighting, and renders a Markdown scorecard section suitable for appending to
``BENCHMARKS.md``.

Sub-score sources
-----------------
1. **Performance (Oracle axis)** — pytest-benchmark JSON output for each
   instrumented operation, compared against ``benchmarks/baselines.json``
   (committed by BM-27, EPIC-19). Piecewise-linear normalization per
   BENCHMARK_SCORING.md §1.

2. **Retrieval-accuracy (Mem0 axis)** — BM-17 deterministic output
   (Recall@k, 0-1) and, optionally, BM-18 LLM-judged accuracy (0-100 %).
   CI-gate consumers must never receive the judged half. Spec: §2.

3. **Agent-quality (Microsoft axis)** — EPIC-21 AGQ-2/3/4 output:
   Pass¹ rate, groundedness mean, coherence mean, fluency mean (1-5 Likert
   → 0-100 via ×20). Nightly-only. Spec: §3.

Weight configuration
--------------------
Loaded from ``benchmarks/scoring_weights.yaml``.  The file MUST exist; there is
no silent fallback — a missing or invalid config is an error, not a default.

Missing input policy
--------------------
A missing sub-score is reported as MISSING, not as zero.  MBS is only computed
when all three sub-scores are fully available; otherwise the composite is
reported as INCOMPLETE.

Usage (standalone CLI)
----------------------
::

    python -m benchmarks.common.scorecard \\
        --perf-json   .benchmarks/benchmark.json \\
        --baselines   benchmarks/baselines.json \\
        --bm17-json   benchmarks/quality/bm17_results.json \\
        --bm18-json   benchmarks/quality/bm18_results.json \\
        --agq-json    benchmarks/quality/agq_results.json \\
        --weights     benchmarks/scoring_weights.yaml \\
        --output      project-management/scorecard_output.md \\
        --append-to   project-management/BENCHMARKS.md

Usage (library)
---------------
::

    from benchmarks.common.scorecard import compute_scorecard, render_markdown

    sc = compute_scorecard(
        perf_json=...,
        baselines_json=...,
        bm17_json=...,
        bm18_json=None,   # not available on this run — will be MISSING
        agq_json=None,    # not available on this run — will be MISSING
        weights_yaml=...,
    )
    md = render_markdown(sc)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# YAML loading — optional dependency; fallback to manual parse for simple schema
# ---------------------------------------------------------------------------

try:
    import yaml as _yaml  # type: ignore[import-untyped]

    def _load_yaml(text: str) -> Any:
        return _yaml.safe_load(text)

except ImportError:
    import re as _re

    def _load_yaml(text: str) -> Any:
        """Minimal YAML parser for the two-level ``weights:`` schema only."""
        result: dict[str, Any] = {}
        current_key: str | None = None
        for raw_line in text.splitlines():
            line = raw_line.split("#")[0]  # strip comments
            stripped = line.strip()
            if not stripped:
                continue
            if ":" in stripped:
                parts = stripped.split(":", 1)
                key = parts[0].strip()
                value_str = parts[1].strip()
                if value_str:
                    try:
                        result.setdefault(current_key or "__top__", {})[key] = float(
                            value_str
                        )
                    except ValueError:
                        result[key] = value_str
                else:
                    current_key = key
                    result[key] = {}
        # unwrap top-level if nested under a non-"__top__" key
        return result


# ---------------------------------------------------------------------------
# Normalization helpers — see BENCHMARK_SCORING.md §1, §2, §3
# ---------------------------------------------------------------------------


def _normalize_op_score(pct: float) -> float:
    """Piecewise-linear normalization for a single operation's latency ratio.

    Anchors (from BENCHMARK_SCORING.md §1 and BM-20 thresholds):
        pct ≤ 1.0  → 100
        pct = 1.5  → 75   (alert threshold; 25-point drop over 0.5 range)
        pct ≥ 3.0  → 0    (fail threshold; 75-point drop over 1.5 range)

    Segment [1.0, 1.5]: drop of 25 over range 0.5 → slope = 25/0.5 = 50 pts/unit
    Segment [1.5, 3.0]: drop of 75 over range 1.5 → slope = 75/1.5 = 50 pts/unit
    """
    if pct <= 1.0:
        return 100.0
    if pct <= 1.5:
        # linear: [1.0 → 100, 1.5 → 75]  drop = 25 over range 0.5
        return 100.0 - 25.0 * (pct - 1.0) / 0.5
    if pct >= 3.0:
        return 0.0
    # linear: [1.5 → 75, 3.0 → 0]  drop = 75 over range 1.5
    return 75.0 - 75.0 * (pct - 1.5) / 1.5


def _likert_to_0_100(mean_1_5: float) -> float:
    """Convert a 1-5 Likert mean to 0-100 (×20).

    Per BENCHMARK_SCORING.md §3: 1→20, 3→60 (pass threshold), 5→100.
    """
    return mean_1_5 * 20.0


# ---------------------------------------------------------------------------
# Weight loading
# ---------------------------------------------------------------------------


def _load_weights(weights_path: Path) -> dict[str, float]:
    """Load and validate ``benchmarks/scoring_weights.yaml``.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If the weights don't sum to 1.0 ±0.001, or if required keys are missing.
    """
    text = weights_path.read_text(encoding="utf-8")
    parsed = _load_yaml(text)
    if not isinstance(parsed, dict) or "weights" not in parsed:
        raise ValueError(
            f"{weights_path}: expected top-level 'weights:' key, got {list(parsed)}"
        )
    w = parsed["weights"]
    if not isinstance(w, dict):
        raise ValueError(f"{weights_path}: 'weights' must be a mapping, got {type(w)}")

    required = {"performance", "retrieval_accuracy", "agent_quality"}
    missing = required - set(w)
    if missing:
        raise ValueError(
            f"{weights_path}: missing weight keys: {sorted(missing)}"
        )

    total = sum(float(v) for v in w.values())
    if abs(total - 1.0) > 0.001:
        raise ValueError(
            f"{weights_path}: weights must sum to 1.0 (±0.001), got {total:.6f}"
        )

    return {k: float(v) for k, v in w.items()}


# ---------------------------------------------------------------------------
# Sub-score computation
# ---------------------------------------------------------------------------


@dataclass
class PerformanceScore:
    """Performance sub-score (Oracle axis)."""

    score: float | None  # None → MISSING
    per_op: dict[str, float] = field(default_factory=dict)
    missing_reason: str | None = None

    @property
    def is_available(self) -> bool:
        return self.score is not None


@dataclass
class RetrievalScore:
    """Retrieval-accuracy sub-score (Mem0 axis)."""

    det_score: float | None  # deterministic (Recall@k ×100)
    judged_score: float | None  # LLM-judged BM-18 accuracy
    missing_det_reason: str | None = None
    missing_judged_reason: str | None = None

    @property
    def composite(self) -> float | None:
        """Full composite (det + judged), or None if deterministic is missing."""
        if self.det_score is None:
            return None
        if self.judged_score is None:
            return None
        return 0.5 * self.det_score + 0.5 * self.judged_score

    @property
    def partial(self) -> float | None:
        """Deterministic-only partial score (for CI runs without BM-18)."""
        return self.det_score

    @property
    def is_fully_available(self) -> bool:
        return self.det_score is not None and self.judged_score is not None

    @property
    def is_partially_available(self) -> bool:
        return self.det_score is not None and self.judged_score is None


@dataclass
class AgentQualityScore:
    """Agent-quality sub-score (Microsoft axis)."""

    score: float | None  # None → MISSING
    pass1_pct: float | None = None  # Pass¹ ×100
    pass5_pct: float | None = None  # Pass⁵ ×100 (supplementary only)
    groundedness_norm: float | None = None  # 1-5 Likert × 20
    coherence_norm: float | None = None
    fluency_norm: float | None = None
    missing_reason: str | None = None

    @property
    def is_available(self) -> bool:
        return self.score is not None


@dataclass
class ScorecardResult:
    """Complete result of a scorecard computation run."""

    perf: PerformanceScore
    retrieval: RetrievalScore
    agent_quality: AgentQualityScore
    weights: dict[str, float]
    mbs: float | None  # None → INCOMPLETE
    mbs_partial: float | None  # deterministic-only composite (no BM-18, no AGQ)
    run_date: str = field(default_factory=lambda: date.today().isoformat())
    run_mode: str = "unknown"  # "full", "partial", "incomplete"


def _compute_performance(
    perf_json: dict[str, Any] | None,
    baselines_json: dict[str, Any] | None,
) -> PerformanceScore:
    """Compute P-score from pytest-benchmark JSON and baselines.

    Args:
        perf_json:
            Parsed ``pytest-benchmark`` JSON (``{"benchmarks": [...]}``) or
            None if the file is missing/unavailable.
        baselines_json:
            Parsed ``benchmarks/baselines.json`` (``{"<name>": <threshold_s>}``)
            or None if missing.

    Returns:
        PerformanceScore with either a computed score or a MISSING reason.
    """
    if baselines_json is None:
        return PerformanceScore(
            score=None,
            missing_reason="BM-27 baselines not committed or baselines.json not found",
        )
    if perf_json is None:
        return PerformanceScore(
            score=None,
            missing_reason="pytest-benchmark JSON not found",
        )

    benchmarks = perf_json.get("benchmarks", [])
    per_op: dict[str, float] = {}
    op_scores: list[float] = []

    for bench in benchmarks:
        name: str = bench.get("fullname") or bench.get("name") or ""
        stats = bench.get("stats") or {}
        mean_s: float | None = stats.get("mean")
        if mean_s is None:
            continue

        # Match by any suffix of the benchmark name against baselines keys
        baseline: float | None = None
        for key, val in baselines_json.items():
            if name.endswith(key) or key in name:
                baseline = float(val)
                break
        if baseline is None:
            continue  # not in baseline set — skip

        pct = mean_s / baseline
        op_score = _normalize_op_score(pct)
        per_op[name] = op_score
        op_scores.append(op_score)

    if not op_scores:
        return PerformanceScore(
            score=None,
            missing_reason=(
                "no operations matched between pytest-benchmark JSON and baselines.json"
            ),
        )

    return PerformanceScore(
        score=sum(op_scores) / len(op_scores),
        per_op=per_op,
    )


def _compute_retrieval(
    bm17_json: dict[str, Any] | None,
    bm18_json: dict[str, Any] | None,
) -> RetrievalScore:
    """Compute R-score from BM-17 (deterministic) and BM-18 (judged).

    Args:
        bm17_json:
            Parsed BM-17 output with at least ``{"recall_at_k": float}``.
        bm18_json:
            Parsed BM-18 output with at least ``{"accuracy": float}`` (0-100).
            Pass None if not available on this run (CI run without nightly data).

    Returns:
        RetrievalScore with det_score and/or judged_score populated.
    """
    det_score: float | None = None
    missing_det: str | None = None
    judged_score: float | None = None
    missing_judged: str | None = None

    if bm17_json is None:
        missing_det = "BM-17 output not found"
    else:
        recall = bm17_json.get("recall_at_k")
        if recall is None:
            missing_det = "BM-17 JSON missing 'recall_at_k' key"
        else:
            det_score = float(recall) * 100.0  # 0-1 → 0-100

    if bm18_json is None:
        missing_judged = "BM-18 output not found — nightly only"
    else:
        acc = bm18_json.get("accuracy")
        if acc is None:
            missing_judged = "BM-18 JSON missing 'accuracy' key"
        else:
            judged_score = float(acc)  # already 0-100

    return RetrievalScore(
        det_score=det_score,
        judged_score=judged_score,
        missing_det_reason=missing_det,
        missing_judged_reason=missing_judged,
    )


def _compute_agent_quality(
    agq_json: dict[str, Any] | None,
) -> AgentQualityScore:
    """Compute A-score from EPIC-21 AGQ-2/3/4 output.

    Args:
        agq_json:
            Parsed AGQ output with keys:
            ``{"pass1_rate": float, "pass5_rate": float,
               "groundedness_mean": float, "coherence_mean": float,
               "fluency_mean": float}``
            All 1-5 Likert fields normalized via ×20.

    Returns:
        AgentQualityScore with computed score or MISSING reason.
    """
    if agq_json is None:
        return AgentQualityScore(
            score=None,
            missing_reason="EPIC-21 AGQ suite not yet run",
        )

    required = {
        "pass1_rate",
        "groundedness_mean",
        "coherence_mean",
        "fluency_mean",
    }
    missing_keys = required - set(agq_json)
    if missing_keys:
        return AgentQualityScore(
            score=None,
            missing_reason=(
                f"AGQ JSON missing required keys: {sorted(missing_keys)}"
            ),
        )

    pass1_pct = float(agq_json["pass1_rate"]) * 100.0
    pass5_pct = (
        float(agq_json["pass5_rate"]) * 100.0 if "pass5_rate" in agq_json else None
    )
    groundedness_norm = _likert_to_0_100(float(agq_json["groundedness_mean"]))
    coherence_norm = _likert_to_0_100(float(agq_json["coherence_mean"]))
    fluency_norm = _likert_to_0_100(float(agq_json["fluency_mean"]))

    # A-score formula (BENCHMARK_SCORING.md §3):
    a_score = (pass1_pct + groundedness_norm + coherence_norm + fluency_norm) / 4.0

    return AgentQualityScore(
        score=a_score,
        pass1_pct=pass1_pct,
        pass5_pct=pass5_pct,
        groundedness_norm=groundedness_norm,
        coherence_norm=coherence_norm,
        fluency_norm=fluency_norm,
    )


# ---------------------------------------------------------------------------
# Top-level computation
# ---------------------------------------------------------------------------


def compute_scorecard(
    *,
    perf_json: dict[str, Any] | None,
    baselines_json: dict[str, Any] | None,
    bm17_json: dict[str, Any] | None,
    bm18_json: dict[str, Any] | None,
    agq_json: dict[str, Any] | None,
    weights: dict[str, float],
) -> ScorecardResult:
    """Compute MBS from all three sub-score sources.

    All inputs are parsed JSON dicts (or None when unavailable).  The caller is
    responsible for loading the JSON files and the weight config; this function
    is deterministic given fixed inputs and is unit-testable without any live
    Db2 instance or LLM judge.

    Args:
        perf_json:       pytest-benchmark JSON (EPIC-14/15). None → P-score MISSING.
        baselines_json:  benchmarks/baselines.json (BM-27). None → P-score MISSING.
        bm17_json:       BM-17 deterministic IR output. None → R-score det MISSING.
        bm18_json:       BM-18 LLM-judged output. None → R-score judged MISSING.
        agq_json:        EPIC-21 AGQ-2/3/4 output. None → A-score MISSING.
        weights:         Validated weight dict from scoring_weights.yaml.

    Returns:
        ScorecardResult with all sub-scores and the composite MBS.
    """
    perf = _compute_performance(perf_json, baselines_json)
    retrieval = _compute_retrieval(bm17_json, bm18_json)
    agent_quality = _compute_agent_quality(agq_json)

    w_p = weights["performance"]
    w_r = weights["retrieval_accuracy"]
    w_a = weights["agent_quality"]

    # Full composite: requires all three sub-scores fully available
    # (retrieval must have both det + judged components)
    mbs: float | None = None
    if (
        perf.is_available
        and retrieval.is_fully_available
        and agent_quality.is_available
    ):
        r_full = retrieval.composite  # type: ignore[assignment]
        assert r_full is not None  # guaranteed by is_fully_available
        mbs = w_p * perf.score + w_r * r_full + w_a * agent_quality.score  # type: ignore[operator]

    # Partial composite: P-score + deterministic R-score only (no BM-18, no AGQ)
    # This is valid for CI runs; it is labelled "partial" in the output.
    mbs_partial: float | None = None
    if perf.is_available and retrieval.is_partially_available:
        # Renormalize weights to the two available axes
        total_w = w_p + w_r
        if total_w > 0:
            mbs_partial = (
                (w_p / total_w) * perf.score  # type: ignore[operator]
                + (w_r / total_w) * retrieval.partial  # type: ignore[operator]
            )

    run_mode: str
    if mbs is not None:
        run_mode = "full"
    elif mbs_partial is not None:
        run_mode = "partial"
    else:
        run_mode = "incomplete"

    return ScorecardResult(
        perf=perf,
        retrieval=retrieval,
        agent_quality=agent_quality,
        weights=weights,
        mbs=mbs,
        mbs_partial=mbs_partial,
        run_mode=run_mode,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _mbs_status(score: float) -> str:
    if score >= 80:
        return "🟢 Healthy"
    if score >= 60:
        return "🟡 Monitor"
    return "🔴 Alert"


def render_markdown(sc: ScorecardResult) -> str:
    """Render a ScorecardResult as a Markdown scorecard section.

    The output is designed to be appended to BENCHMARKS.md as the
    ``## Composite Scorecard (MBS)`` section (BM-26 structure).  It never
    replaces existing per-suite sections.
    """
    lines: list[str] = []
    lines.append("---")
    lines.append("")
    lines.append("## Composite Scorecard (Memory Benchmark Score)")
    lines.append("")
    lines.append(
        f"> **Run date:** {sc.run_date}  "
        f"**Mode:** {sc.run_mode}"
    )
    lines.append("")

    # --- MBS headline ---
    if sc.mbs is not None:
        status = _mbs_status(sc.mbs)
        lines.append(f"| **Memory Benchmark Score (MBS)** | **{sc.mbs:.1f} / 100** | {status} |")
        lines.append("|---|---|---|")
    elif sc.mbs_partial is not None:
        lines.append(
            f"| **MBS (partial — P + R-det only)** | **{sc.mbs_partial:.1f} / 100** "
            "| ⚠️ Partial — AGQ and/or BM-18 inputs missing |"
        )
        lines.append("|---|---|---|")
    else:
        lines.append("| **MBS** | **INCOMPLETE** | ⚠️ One or more sub-scores missing — see details below |")
        lines.append("|---|---|---|")

    lines.append("")
    lines.append("### Sub-scores")
    lines.append("")
    lines.append("| Sub-score | Axis | Score | Notes |")
    lines.append("|---|---|---|---|")

    # Performance row
    if sc.perf.is_available:
        p_note = ""
        if sc.perf.score is not None and sc.perf.score < 75:
            p_note = "⚠️ Below alert floor (< 75) — at least one op at 150% baseline"
        lines.append(
            f"| Performance | Oracle | {sc.perf.score:.1f} | {p_note} |"
        )
    else:
        lines.append(
            f"| Performance | Oracle | MISSING | {sc.perf.missing_reason} |"
        )

    # Retrieval-accuracy rows
    if sc.retrieval.det_score is not None:
        det_note = ""
        if sc.retrieval.det_score < 80:
            det_note = "⚠️ Below healthy floor (< 80) — Recall@k degraded"
        lines.append(
            f"| Retrieval-accuracy (deterministic) | Mem0 | {sc.retrieval.det_score:.1f} | "
            f"CI-gatable {det_note} |"
        )
    else:
        lines.append(
            f"| Retrieval-accuracy (deterministic) | Mem0 | MISSING | "
            f"{sc.retrieval.missing_det_reason} |"
        )

    if sc.retrieval.judged_score is not None:
        lines.append(
            f"| Retrieval-accuracy (judged) | Mem0 | {sc.retrieval.judged_score:.1f} | "
            f"Nightly-only; not a CI gate |"
        )
    else:
        lines.append(
            f"| Retrieval-accuracy (judged) | Mem0 | MISSING | "
            f"{sc.retrieval.missing_judged_reason} |"
        )

    if sc.retrieval.is_fully_available and sc.retrieval.composite is not None:
        lines.append(
            f"| **R-score composite** | Mem0 | **{sc.retrieval.composite:.1f}** | "
            f"0.5 × det + 0.5 × judged |"
        )
    elif sc.retrieval.is_partially_available and sc.retrieval.partial is not None:
        lines.append(
            f"| **R-score (partial, deterministic only)** | Mem0 | **{sc.retrieval.partial:.1f}** | "
            f"BM-18 not available on this run |"
        )

    # Agent-quality row
    if sc.agent_quality.is_available:
        a_note = ""
        if sc.agent_quality.score is not None and sc.agent_quality.score < 60:
            a_note = "⚠️ Below Foundry pass threshold (< 60 = 3.0 × 20)"
        lines.append(
            f"| Agent-quality | Microsoft | {sc.agent_quality.score:.1f} | "
            f"Nightly-only {a_note} |"
        )
        # Show breakdown
        if sc.agent_quality.pass1_pct is not None:
            lines.append("")
            lines.append("#### Agent-quality breakdown")
            lines.append("")
            lines.append("| Metric | Normalized (0-100) | Source |")
            lines.append("|---|---|---|")
            lines.append(
                f"| Pass¹ rate | {sc.agent_quality.pass1_pct:.1f} | AGQ-2 (EPIC-21) |"
            )
            if sc.agent_quality.pass5_pct is not None:
                lines.append(
                    f"| Pass⁵ rate (supplementary) | {sc.agent_quality.pass5_pct:.1f} | "
                    f"AGQ-2 (EPIC-21) — not in formula |"
                )
            if sc.agent_quality.groundedness_norm is not None:
                lines.append(
                    f"| Groundedness | {sc.agent_quality.groundedness_norm:.1f} | AGQ-3 (EPIC-21) |"
                )
            if sc.agent_quality.coherence_norm is not None:
                lines.append(
                    f"| Coherence | {sc.agent_quality.coherence_norm:.1f} | AGQ-4 (EPIC-21) |"
                )
            if sc.agent_quality.fluency_norm is not None:
                lines.append(
                    f"| Fluency | {sc.agent_quality.fluency_norm:.1f} | AGQ-4 (EPIC-21) |"
                )
    else:
        lines.append(
            f"| Agent-quality | Microsoft | MISSING | "
            f"{sc.agent_quality.missing_reason} |"
        )

    # Weight footnote
    lines.append("")
    lines.append(
        f"> **Weights:** performance={sc.weights['performance']:.3f}  "
        f"retrieval_accuracy={sc.weights['retrieval_accuracy']:.3f}  "
        f"agent_quality={sc.weights['agent_quality']:.3f}  "
        f"(from `benchmarks/scoring_weights.yaml`)"
    )
    lines.append(
        "> See [`project-management/BENCHMARK_SCORING.md`](./BENCHMARK_SCORING.md) "
        "for the full scoring model."
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def _load_json_or_none(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[return-value]


def _append_scorecard_to_benchmarks(md_path: Path, scorecard_md: str) -> None:
    """Append a scorecard Markdown block to BENCHMARKS.md.

    Replaces any existing ``## Composite Scorecard`` section to avoid
    accumulating stale entries; appends at the end otherwise.
    """
    existing = md_path.read_text(encoding="utf-8")
    marker = "\n---\n\n## Composite Scorecard (Memory Benchmark Score)"
    # Strip any existing scorecard section
    if marker in existing:
        existing = existing[: existing.index(marker)]
        existing = existing.rstrip()
    updated = existing + "\n\n" + scorecard_md
    md_path.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Compute and render the Memory Benchmark Score (MBS). "
            "See project-management/BENCHMARK_SCORING.md for the full model."
        )
    )
    p.add_argument(
        "--perf-json",
        type=Path,
        metavar="FILE",
        help="pytest-benchmark JSON output file (EPIC-14/15).",
    )
    p.add_argument(
        "--baselines",
        type=Path,
        metavar="FILE",
        default=Path("benchmarks/baselines.json"),
        help="benchmarks/baselines.json (BM-27). Default: benchmarks/baselines.json",
    )
    p.add_argument(
        "--bm17-json",
        type=Path,
        metavar="FILE",
        help="BM-17 deterministic IR results JSON.",
    )
    p.add_argument(
        "--bm18-json",
        type=Path,
        metavar="FILE",
        help="BM-18 LLM-judged accuracy JSON (nightly only).",
    )
    p.add_argument(
        "--agq-json",
        type=Path,
        metavar="FILE",
        help="EPIC-21 AGQ-2/3/4 agent-quality results JSON.",
    )
    p.add_argument(
        "--weights",
        type=Path,
        metavar="FILE",
        default=Path("benchmarks/scoring_weights.yaml"),
        help="Weight config file. Default: benchmarks/scoring_weights.yaml",
    )
    p.add_argument(
        "--output",
        type=Path,
        metavar="FILE",
        help="Write rendered Markdown to this file (in addition to stdout).",
    )
    p.add_argument(
        "--append-to",
        type=Path,
        metavar="FILE",
        help=(
            "Append/replace the scorecard section in this Markdown file "
            "(e.g. project-management/BENCHMARKS.md)."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint.  Returns exit code (0=success, 1=error)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --- Load weights (required; hard error if missing or invalid) ---
    weights_path: Path = args.weights
    if not weights_path.exists():
        print(
            f"ERROR: weights file not found: {weights_path}\n"
            "Create benchmarks/scoring_weights.yaml — "
            "see project-management/BENCHMARK_SCORING.md §5.",
            file=sys.stderr,
        )
        return 1
    try:
        weights = _load_weights(weights_path)
    except (ValueError, KeyError) as exc:
        print(f"ERROR loading weights: {exc}", file=sys.stderr)
        return 1

    # --- Load sub-score inputs ---
    perf_json = _load_json_or_none(args.perf_json)
    baselines_json = _load_json_or_none(args.baselines)
    bm17_json = _load_json_or_none(args.bm17_json)
    bm18_json = _load_json_or_none(args.bm18_json)
    agq_json = _load_json_or_none(args.agq_json)

    # --- Compute ---
    sc = compute_scorecard(
        perf_json=perf_json,
        baselines_json=baselines_json,
        bm17_json=bm17_json,
        bm18_json=bm18_json,
        agq_json=agq_json,
        weights=weights,
    )

    # --- Render ---
    md = render_markdown(sc)
    print(md)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md, encoding="utf-8")
        print(f"\n[scorecard written to {args.output}]", file=sys.stderr)

    if args.append_to:
        _append_scorecard_to_benchmarks(args.append_to, md)
        print(f"[scorecard appended/updated in {args.append_to}]", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
benchmarks/agent_quality/run_agq.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
AGQ-5 (EPIC-21): Combine AGQ-2/3/4 JSON outputs into the single
``agq_results.json`` shape consumed by UNI-3's ``scorecard.py``.

This is a simple merge/normalisation step — it reads the three separate output
files produced by the individual suite runners and emits the unified JSON
expected by :func:`~benchmarks.common.scorecard._compute_agent_quality`:

::

    {
        "pass1_rate": 0.75,      # from AGQ-2
        "pass5_rate": 0.60,      # from AGQ-2 (supplementary)
        "groundedness_mean": 3.8, # from AGQ-3
        "coherence_mean": 4.0,   # from AGQ-4
        "fluency_mean": 4.1,     # from AGQ-4
        "judge_model": "llama3.1:8b",
        "seed": 42
    }

Any missing input file is treated as MISSING (its contribution is omitted) rather
than defaulting to zero — consistent with the project-wide "MISSING ≠ zero"
discipline from BENCHMARK_SCORING.md §6.

CLI usage
---------
::

    python benchmarks/agent_quality/run_agq.py \\
        --agq2-json benchmark_results/agq2_flat_context.json \\
        --agq3-json benchmark_results/agq3_groundedness.json \\
        --agq4-json benchmark_results/agq4_coherence.json \\
        --output   benchmark_results/agq_results.json

Nightly CI usage (AGQ-5)
------------------------
The nightly workflow writes each suite's JSON to ``$BENCH_OUT/agq{2,3,4}.json``
and then calls this script to produce ``$BENCH_OUT/agq_results.json``, which is
passed to the scorecard step as ``--agq-json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------


def merge_agq_results(
    agq2: dict[str, Any] | None,
    agq3: dict[str, Any] | None,
    agq4: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge AGQ-2/3/4 sub-results into the unified scorecard JSON shape.

    Missing inputs are omitted — callers should check for missing keys rather
    than assuming all fields are present.

    Args:
        agq2: Parsed AGQ-2 output (``AgentQualityResult.to_dict()``).
              Must contain ``pass1_rate`` and ``pass5_rate``.
        agq3: Parsed AGQ-3 output (``GroundednessRunResult.to_dict()``).
              Must contain ``groundedness_mean``.
        agq4: Parsed AGQ-4 output (``CoherenceRunResult`` dict or equivalent).
              Must contain ``coherence_mean`` and ``fluency_mean``.

    Returns:
        Merged dict ready for ``scorecard._compute_agent_quality()``.
        Only includes keys for which the corresponding input was available.
    """
    merged: dict[str, Any] = {}

    if agq2 is not None:
        if "pass1_rate" in agq2:
            merged["pass1_rate"] = agq2["pass1_rate"]
        if "pass5_rate" in agq2:
            merged["pass5_rate"] = agq2["pass5_rate"]
        if "judge_model" in agq2 and "judge_model" not in merged:
            merged["judge_model"] = agq2["judge_model"]
        if "seed" in agq2 and "seed" not in merged:
            merged["seed"] = agq2["seed"]

    if agq3 is not None:
        if "groundedness_mean" in agq3:
            merged["groundedness_mean"] = agq3["groundedness_mean"]
        if "judge_model" in agq3 and "judge_model" not in merged:
            merged["judge_model"] = agq3["judge_model"]
        if "seed" in agq3 and "seed" not in merged:
            merged["seed"] = agq3["seed"]

    if agq4 is not None:
        if "coherence_mean" in agq4:
            merged["coherence_mean"] = agq4["coherence_mean"]
        if "fluency_mean" in agq4:
            merged["fluency_mean"] = agq4["fluency_mean"]
        if "judge_model" in agq4 and "judge_model" not in merged:
            merged["judge_model"] = agq4["judge_model"]

    return merged


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def _load_json_or_none(path: Path | None) -> dict[str, Any] | None:
    """Load a JSON file, returning ``None`` if the path is absent or None."""
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[return-value]
    except json.JSONDecodeError as exc:
        logger.warning("AGQ merge: could not parse %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "AGQ-5 (EPIC-21): Merge AGQ-2/3/4 outputs into a single "
            "agq_results.json for the UNI-3 scorecard. "
            "Missing inputs are reported as MISSING, not zero."
        )
    )
    p.add_argument(
        "--agq2-json",
        type=Path,
        metavar="FILE",
        help="AGQ-2 task-completion output JSON (pass1_rate, pass5_rate).",
    )
    p.add_argument(
        "--agq3-json",
        type=Path,
        metavar="FILE",
        help="AGQ-3 groundedness output JSON (groundedness_mean).",
    )
    p.add_argument(
        "--agq4-json",
        type=Path,
        metavar="FILE",
        help="AGQ-4 coherence/fluency output JSON (coherence_mean, fluency_mean).",
    )
    p.add_argument(
        "--output",
        type=Path,
        metavar="FILE",
        help=(
            "Write merged JSON to this file. "
            "If omitted, prints to stdout only."
        ),
    )
    p.add_argument(
        "--fail-on-missing",
        action="store_true",
        default=False,
        help=(
            "Exit non-zero if any of the three required inputs is missing. "
            "By default, missing inputs are reported but do not cause failure."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint.  Returns 0 on success, 1 on error."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)

    agq2 = _load_json_or_none(args.agq2_json)
    agq3 = _load_json_or_none(args.agq3_json)
    agq4 = _load_json_or_none(args.agq4_json)

    missing: list[str] = []
    if agq2 is None:
        missing.append("AGQ-2 (pass1_rate/pass5_rate)")
    if agq3 is None:
        missing.append("AGQ-3 (groundedness_mean)")
    if agq4 is None:
        missing.append("AGQ-4 (coherence_mean/fluency_mean)")

    if missing:
        msg = "MISSING inputs: " + ", ".join(missing)
        if args.fail_on_missing:
            print(f"ERROR: {msg}", file=sys.stderr)
            return 1
        logger.warning("%s — merged output will be partial.", msg)

    merged = merge_agq_results(agq2, agq3, agq4)
    output_json = json.dumps(merged, indent=2)
    print(output_json)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_json + "\n", encoding="utf-8")
        logger.info("AGQ merged results written to %s", args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())

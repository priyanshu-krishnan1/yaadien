#!/usr/bin/env python3
"""
scripts/generate_consolidated_summary.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Render the "Consolidated Benchmark Report" markdown written to
``$GITHUB_STEP_SUMMARY`` by the ``consolidated-report`` job in
``.github/workflows/benchmark-suite.yml``.

Extracted from a ~140-line inline bash heredoc (six near-identical
copy-pasted per-tier sections) into one function per tier, so a change to
the report shape is a one-place Python edit instead of a shell/YAML edit.
Behavior is intended to be identical to the bash it replaces, including the
oddity that the two Locust sections gate on "does a CSV exist on disk"
rather than on the job's `result` (the JSON-based sections gate on
`result`) — that inconsistency predates this script and is preserved
rather than silently "fixed" here.

Usage
-----
::

    python scripts/generate_consolidated_summary.py \\
        --consolidated-dir consolidated \\
        --run-number "$GITHUB_RUN_NUMBER" \\
        --sha "$GITHUB_SHA" \\
        --ref "$GITHUB_REF_NAME" \\
        --event-name "$GITHUB_EVENT_NAME" \\
        --run-url "$RUN_URL" \\
        --scenario "${{ inputs.scenario }}" \\
        --corpus-size "${{ inputs.corpus_size }}" \\
        --dry-run "${{ inputs.dry_run }}" \\
        --result codspeed=success \\
        --result benchmark=failure \\
        --result locust-isolation=success \\
        --result locust-scale=success \\
        --result benchmark-nightly=success \\
        --result benchmark-scale=skipped \\
        >> "$GITHUB_STEP_SUMMARY"

stdlib only — no third-party dependencies. Shells out to
``generate_benchmark_summary.py`` for pytest-benchmark JSON rendering,
matching what the bash it replaces did.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SUMMARY_SCRIPT = _HERE / "generate_benchmark_summary.py"


def _render_json(json_path: Path) -> str:
    """Run generate_benchmark_summary.py against *json_path*, return its stdout.

    Mirrors the bash: on a non-zero exit or missing file, return a
    "Could not parse" note instead of raising.
    """
    try:
        result = subprocess.run(
            [sys.executable, str(_SUMMARY_SCRIPT), str(json_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except (subprocess.CalledProcessError, OSError):
        return f"> Could not parse {json_path}\n"


def _render_csv_block(csv_path: Path) -> str:
    label = csv_path.name.removesuffix("_stats.csv")
    lines = csv_path.read_text(encoding="utf-8").splitlines()[:3]
    body = "\n".join(lines)
    return f"### {label}\n```\n{body}\n```\n"


def _codspeed_section(result: str) -> str:
    if result == "success":
        status = "| ✅ Passed |"
    elif result == "skipped":
        status = "| ⏭️ Skipped |"
    else:
        status = f"| ❌ {result} |"
    return (
        "## Tier 0 — CodSpeed micro-benchmark smoke test\n\n"
        "> **Job:** `codspeed`  |  Origin: `benchmark-pr.yml`\n"
        "> Results for this tier are reported directly in the CodSpeed job log.\n"
        "| Status |\n"
        "|--------|\n"
        f"{status}\n\n---\n"
    )


def _tier01_section(consolidated_dir: Path, result: str) -> str:
    out = [
        "## Tier 0/1 — Benchmark suite (Db2 LUW + pytest-benchmark)\n",
        "> **Job:** `benchmark`  |  Origin: prior `benchmarks.yml`\n",
    ]
    if result in ("success", "failure"):
        for name in ("output_tier0.json", "output_tier1.json"):
            json_path = consolidated_dir / "benchmark" / name
            if json_path.is_file():
                label = json_path.stem.removeprefix("output_")
                out.append(f"### {label}")
                out.append(_render_json(json_path))
    else:
        out.append(f"> Job result: `{result}` — no artifacts available.")
    out.append("---\n")
    return "\n".join(out)


def _locust_section(title: str, job: str, origin: str, dir_name: str, consolidated_dir: Path, result: str) -> str:
    out = [
        f"## {title}\n",
        f"> **Job:** `{job}`  |  Origin: {origin}\n",
    ]
    csv_dir = consolidated_dir / dir_name
    csvs = sorted(csv_dir.glob("*_stats.csv")) if csv_dir.is_dir() else []
    if csvs:
        for csv_path in csvs:
            out.append(_render_csv_block(csv_path))
    else:
        out.append(f"> Job result: `{result}` — no CSV artifacts found.")
    out.append("---\n")
    return "\n".join(out)


def _single_json_section(title: str, job: str, origin: str, json_path: Path, result: str, missing_note: str) -> str:
    out = [
        f"## {title}\n",
        f"> **Job:** `{job}`  |  Origin: {origin}\n",
    ]
    if json_path.is_file():
        out.append(_render_json(json_path))
    else:
        out.append(f"> Job result: `{result}` — {missing_note}")
    out.append("---\n")
    return "\n".join(out)


def _overall_table(results: dict[str, str]) -> str:
    rows = [
        ("codspeed", "Tier 0 CodSpeed"),
        ("benchmark", "Tier 0/1 pytest-benchmark"),
        ("locust-isolation", "BM-12/13"),
        ("locust-scale", "BM-14/15"),
        ("benchmark-nightly", "Tier 2"),
        ("benchmark-scale", "Tier 3 live"),
    ]
    lines = ["## Overall job results\n", "| Job | Result |", "|-----|--------|"]
    for job, label in rows:
        lines.append(f"| `{job}` ({label}) | {results.get(job, 'unknown')} |")
    lines.append("")
    return "\n".join(lines)


def render(args: argparse.Namespace) -> str:
    consolidated_dir = Path(args.consolidated_dir)
    results = dict(kv.split("=", 1) for kv in args.result)

    header = (
        "# Consolidated Benchmark Report\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        f"| **Workflow run** | [{args.run_number}]({args.run_url}) |\n"
        f"| **Commit** | `{args.sha}` |\n"
        f"| **Ref** | `{args.ref}` |\n"
        f"| **Triggered by** | `{args.event_name}` |\n"
        f"| **Scenario** | `{args.scenario}` |\n"
        f"| **Corpus size** | `{args.corpus_size}` |\n"
        f"| **Dry run** | `{args.dry_run}` |\n\n---\n\n"
    )

    sections = [
        header,
        _codspeed_section(results.get("codspeed", "unknown")),
        _tier01_section(consolidated_dir, results.get("benchmark", "unknown")),
        _locust_section(
            "Locust isolation gate (BM-12 + BM-13)",
            "locust-isolation",
            "prior `benchmarks.yml`",
            "locust-isolation",
            consolidated_dir,
            results.get("locust-isolation", "unknown"),
        ),
        _locust_section(
            "Locust scalability sweeps + pool saturation (BM-14 + BM-15)",
            "locust-scale",
            "prior `benchmarks.yml`",
            "locust-scale",
            consolidated_dir,
            results.get("locust-scale", "unknown"),
        ),
        _single_json_section(
            "Nightly benchmark suite — Tier 2 (BM-21)",
            "benchmark-nightly",
            "`benchmark-nightly.yml`",
            consolidated_dir / "benchmark-nightly" / "output_benchmark.json",
            results.get("benchmark-nightly", "unknown"),
            "no benchmark JSON artifact found.",
        ),
        _single_json_section(
            "Tier 3 — Scale benchmark (live Db2)",
            "benchmark-scale",
            "`benchmark-scale.yml`",
            consolidated_dir / "benchmark-scale" / "output_scale.json",
            results.get("benchmark-scale", "unknown"),
            "Tier 3 did not produce an artifact (timed out, was skipped, dry_run=true, or failed before uploading).",
        ),
        _overall_table(results),
    ]
    return "\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consolidated-dir", default="consolidated")
    parser.add_argument("--run-number", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--scenario", default="full")
    parser.add_argument("--corpus-size", default="50k")
    parser.add_argument("--dry-run", default="false")
    parser.add_argument(
        "--result",
        action="append",
        default=[],
        metavar="JOB=RESULT",
        help="Repeatable. One per job, e.g. --result codspeed=success",
    )
    args = parser.parse_args()
    print(render(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())

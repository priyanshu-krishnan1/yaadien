#!/usr/bin/env python3
"""
scripts/locust_csv_to_jsonl.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Convert Locust ``*_stats.csv`` files into the shared benchmark envelope schema
(``results.jsonl``).

For each row in each stats CSV the following records are emitted:

* ``{name}_rps``      — Requests/s column → unit ``req/s``
* ``{name}_fail_pct`` — failure percentage computed from ``Failure Count`` /
                         ``Request Count`` × 100 → unit ``%``
                         status ``pass`` if fail_pct < 1.0, else ``fail``

Usage
-----
::

    python scripts/locust_csv_to_jsonl.py \\
        --csv-dir locust_results/ \\
        --suite locust-isolation \\
        --run-number 123 \\
        --sha abc123def \\
        --out locust_results/results.jsonl

stdlib only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Turn an arbitrary Locust endpoint name into a safe metric key."""
    return (
        name.strip()
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace(".", "_")
        .replace("-", "_")
        .strip("_")
    ) or "unknown"


def _parse_stats_csv(path: Path, suite: str, run_number: str, sha: str) -> list[dict]:
    """Parse a single Locust ``*_stats.csv`` file and return envelope records."""
    records: list[dict] = []
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha12 = sha[:12] if sha else ""

    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                raw_name = row.get("Name", "").strip()
                # Skip the Locust "Aggregated" summary row — it's a rollup of all rows
                if raw_name.lower() == "aggregated":
                    continue

                slug = _slugify(raw_name) or _slugify(row.get("Type", "unknown"))

                # ── Requests/s ───────────────────────────────────────────────
                rps_raw = row.get("Requests/s", "").strip()
                try:
                    rps = float(rps_raw)
                except (ValueError, TypeError):
                    rps = 0.0
                records.append({
                    "suite": suite,
                    "metric": f"{slug}_rps",
                    "value": rps,
                    "unit": "req/s",
                    "status": "pass",
                    "run_number": run_number,
                    "sha": sha12,
                    "timestamp": timestamp,
                })

                # ── Fail percentage ──────────────────────────────────────────
                try:
                    req_count = int(row.get("Request Count", 0) or 0)
                    fail_count = int(row.get("Failure Count", 0) or 0)
                    fail_pct = (fail_count / req_count * 100.0) if req_count > 0 else 0.0
                except (ValueError, TypeError, ZeroDivisionError):
                    fail_pct = 0.0
                records.append({
                    "suite": suite,
                    "metric": f"{slug}_fail_pct",
                    "value": round(fail_pct, 4),
                    "unit": "%",
                    "status": "pass" if fail_pct < 1.0 else "fail",
                    "run_number": run_number,
                    "sha": sha12,
                    "timestamp": timestamp,
                })

    except OSError as exc:
        print(f"locust_csv_to_jsonl: cannot read {path}: {exc}", file=sys.stderr)

    return records


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert Locust stats CSVs to shared benchmark envelope JSONL.",
    )
    parser.add_argument(
        "--csv-dir",
        required=True,
        help="Directory containing Locust *_stats.csv files.",
    )
    parser.add_argument(
        "--suite",
        required=True,
        help="Suite name for envelope records (e.g. locust-isolation).",
    )
    parser.add_argument(
        "--run-number",
        default="",
        help="GitHub Actions run number ($GITHUB_RUN_NUMBER).",
    )
    parser.add_argument(
        "--sha",
        default="",
        help="Git commit SHA ($GITHUB_SHA).",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output path for results.jsonl.",
    )
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    if not csv_dir.is_dir():
        print(
            f"locust_csv_to_jsonl: directory not found: {csv_dir}",
            file=sys.stderr,
        )
        sys.exit(0)  # non-fatal — produces an empty file

    csv_files = sorted(csv_dir.glob("*_stats.csv"))
    if not csv_files:
        print(
            f"locust_csv_to_jsonl: no *_stats.csv files found in {csv_dir}",
            file=sys.stderr,
        )

    all_records: list[dict] = []
    for csv_path in csv_files:
        all_records.extend(
            _parse_stats_csv(csv_path, args.suite, args.run_number, args.sha)
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in all_records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(
        f"locust_csv_to_jsonl: wrote {len(all_records)} records to {out_path}",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()

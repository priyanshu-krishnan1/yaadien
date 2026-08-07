#!/usr/bin/env python3
"""
scripts/generate_benchmark_summary.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Parse a ``pytest-benchmark`` JSON output file and produce either:

* Markdown (default) — written to stdout, intended for ``$GITHUB_STEP_SUMMARY``
  so the formatted table appears on the GitHub Actions run summary page.

* HTML (``--html``) — a self-contained single-page report with the same table
  plus embedded run metadata, suitable for publishing to GitHub Pages.

Usage
-----
::

    # Markdown → $GITHUB_STEP_SUMMARY
    python scripts/generate_benchmark_summary.py benchmark_results/output.json \
        >> "$GITHUB_STEP_SUMMARY"

    # HTML → benchmark_results/index.html
    python scripts/generate_benchmark_summary.py benchmark_results/output.json \
        --html \
        --run-id  "$GITHUB_RUN_ID" \
        --run-number "$GITHUB_RUN_NUMBER" \
        --sha  "$GITHUB_SHA" \
        --ref  "$GITHUB_REF_NAME" \
        > benchmark_results/index.html

Exit codes
----------
0 — output produced successfully.
1 — the input JSON file does not exist or cannot be parsed; a short error
    message is written to stderr and a minimal placeholder is written to stdout
    so the calling shell step does not fail just because there are no results.

stdlib only — no third-party dependencies.
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ns_to_ms(ns: float) -> str:
    """Format nanoseconds as milliseconds, auto-selecting µs for sub-ms values."""
    if ns < 1_000:
        return f"{ns:.1f} ns"
    if ns < 1_000_000:
        return f"{ns / 1_000:.2f} µs"
    return f"{ns / 1_000_000:.3f} ms"


def _load(path: Path) -> dict:
    """Load and return the top-level JSON dict from *path*."""
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _load_merged(paths: list[Path]) -> tuple[dict, list[dict]]:
    """Load and merge one or more pytest-benchmark JSON files.

    Returns the metadata dict from the first file that exists (for machine/
    Python info) and a combined, sorted list of row dicts from all files.
    """
    meta: dict = {}
    rows: list[dict] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            data = _load(path)
        except (json.JSONDecodeError, OSError):
            continue
        if not meta:
            meta = data  # keep first file's metadata (machine/python info)
        rows.extend(_extract_rows(data))
    rows.sort(key=lambda r: (r["group"], r["name"]))
    return meta, rows


def _extract_rows(data: dict) -> list[dict]:
    """Return a list of row dicts extracted from the pytest-benchmark JSON."""
    benchmarks = data.get("benchmarks", [])
    rows = []
    for b in benchmarks:
        stats = b.get("stats", {})
        params = b.get("params") or {}
        rows.append(
            {
                "name": b.get("name", ""),
                "group": b.get("group") or params.get("group") or "",
                "min": stats.get("min", 0.0) * 1e9,     # s → ns
                "max": stats.get("max", 0.0) * 1e9,
                "mean": stats.get("mean", 0.0) * 1e9,
                "stddev": stats.get("stddev", 0.0) * 1e9,
                "median": stats.get("median", 0.0) * 1e9,
                "rounds": stats.get("rounds", 0),
                "iterations": stats.get("iterations", 0),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

def _render_markdown(data: dict, rows: list[dict]) -> str:
    machine = data.get("machine_info", {})
    python = data.get("python_info", {})
    commit = data.get("commit_info", {})
    datetime_str = data.get("datetime", "")

    lines: list[str] = []
    lines.append("## Benchmark Results")
    lines.append("")

    # Metadata block
    if datetime_str:
        lines.append(f"**Run:** `{datetime_str}`")
    if commit.get("id"):
        lines.append(f"**Commit:** `{commit['id'][:12]}`"
                     + (f"  ({commit.get('branch', '')})" if commit.get("branch") else ""))
    cpu = machine.get("cpu", {})
    if cpu.get("brand_raw"):
        lines.append(f"**CPU:** {cpu['brand_raw']}")
    if python.get("version"):
        lines.append(f"**Python:** {python['version']}")
    lines.append(f"**Total benchmarks:** {len(rows)}")
    lines.append("")

    if not rows:
        lines.append("> No benchmark results were found in the output file.")
        return "\n".join(lines)

    # Table
    lines.append(
        "| Test | Min | Max | Mean | StdDev | Median | Rounds |"
    )
    lines.append(
        "|:-----|----:|----:|-----:|-------:|-------:|-------:|"
    )
    for r in rows:
        name = r["name"]
        # Keep names reasonably short for the summary table by truncating at 80 chars.
        if len(name) > 80:
            name = name[:77] + "..."
        lines.append(
            f"| `{name}` "
            f"| {_ns_to_ms(r['min'])} "
            f"| {_ns_to_ms(r['max'])} "
            f"| {_ns_to_ms(r['mean'])} "
            f"| {_ns_to_ms(r['stddev'])} "
            f"| {_ns_to_ms(r['median'])} "
            f"| {r['rounds']:,} |"
        )

    lines.append("")
    lines.append(
        "_Times shown in ns / µs / ms as appropriate. "
        "StdDev is the standard deviation across all rounds. "
        "Full JSON output and SVG histograms are available in the "
        "`benchmark-report` artifact._"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>agent-memory-sdk benchmark report — run {run_number}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
           font-size: 14px; line-height: 1.6; color: #1f2328;
           background: #ffffff; margin: 0; padding: 24px; }}
    .container {{ max-width: 1000px; margin: 0 auto; }}
    h1 {{ font-size: 1.4rem; font-weight: 600; margin-bottom: 4px; }}
    .meta {{ color: #57606a; font-size: 13px; margin-bottom: 20px; }}
    .meta span {{ margin-right: 16px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    thead tr {{ background: #f7f8fa; }}
    th {{ padding: 8px 10px; text-align: left; border-bottom: 2px solid #e5e7eb;
         font-weight: 600; white-space: nowrap; }}
    th.num {{ text-align: right; }}
    td {{ padding: 7px 10px; border-bottom: 1px solid #e5e7eb;
         font-family: "SFMono-Regular", Consolas, monospace; font-size: 12px; }}
    td.name {{ font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
               max-width: 400px; overflow-wrap: break-word; }}
    td.num {{ text-align: right; }}
    tr:hover {{ background: #f7f8fa; }}
    .histograms {{ margin-top: 32px; }}
    .histograms h2 {{ font-size: 1.1rem; margin-bottom: 12px; }}
    .histograms ul {{ list-style: none; padding: 0; }}
    .histograms li {{ margin-bottom: 6px; }}
    .histograms a {{ color: #3b82d4; text-decoration: none; }}
    .histograms a:hover {{ text-decoration: underline; }}
    footer {{ margin-top: 40px; padding-top: 12px; border-top: 1px solid #e5e7eb;
              font-size: 12px; color: #57606a; text-align: center; }}
    .empty {{ color: #57606a; font-style: italic; }}
  </style>
</head>
<body>
<div class="container">
  <h1>agent-memory-sdk benchmark report</h1>
  <div class="meta">
    <span>Run <strong>#{run_number}</strong></span>
    {sha_span}
    {ref_span}
    <span>Generated {generated_at}</span>
    {cpu_span}
    {python_span}
  </div>

  <table>
    <thead>
      <tr>
        <th>Test</th>
        <th class="num">Min</th>
        <th class="num">Max</th>
        <th class="num">Mean</th>
        <th class="num">StdDev</th>
        <th class="num">Median</th>
        <th class="num">Rounds</th>
      </tr>
    </thead>
    <tbody>
{table_rows}
    </tbody>
  </table>

{histograms_section}

  <footer>Made with IBM Bob &bull; agent-memory-sdk benchmarks</footer>
</div>
</body>
</html>
"""


def _render_html(
    data: dict,
    rows: list[dict],
    run_id: str,
    run_number: str,
    sha: str,
    ref: str,
) -> str:
    machine = data.get("machine_info", {})
    python_info = data.get("python_info", {})

    cpu = machine.get("cpu", {})
    cpu_str = cpu.get("brand_raw", "")
    py_ver = python_info.get("version", "")

    sha_span = (
        f'<span>Commit <code>{html_module.escape(sha[:12])}</code></span>'
        if sha else ""
    )
    ref_span = (
        f'<span>Branch <code>{html_module.escape(ref)}</code></span>'
        if ref else ""
    )
    cpu_span = (
        f'<span>CPU: {html_module.escape(cpu_str)}</span>'
        if cpu_str else ""
    )
    python_span = (
        f'<span>Python {html_module.escape(py_ver)}</span>'
        if py_ver else ""
    )
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if rows:
        tr_lines = []
        for r in rows:
            name = html_module.escape(r["name"])
            tr_lines.append(
                f'      <tr>'
                f'<td class="name">{name}</td>'
                f'<td class="num">{_ns_to_ms(r["min"])}</td>'
                f'<td class="num">{_ns_to_ms(r["max"])}</td>'
                f'<td class="num">{_ns_to_ms(r["mean"])}</td>'
                f'<td class="num">{_ns_to_ms(r["stddev"])}</td>'
                f'<td class="num">{_ns_to_ms(r["median"])}</td>'
                f'<td class="num">{r["rounds"]:,}</td>'
                f'</tr>'
            )
        table_rows = "\n".join(tr_lines)
    else:
        table_rows = '      <tr><td colspan="7" class="empty">No benchmark results found.</td></tr>'

    # Link to SVG histograms if any exist alongside the index.html
    # (pytest-benchmark generates files named histogram_<name>.svg)
    histograms_section = ""

    return _HTML_TEMPLATE.format(
        run_number=html_module.escape(run_number or "?"),
        sha_span=sha_span,
        ref_span=ref_span,
        cpu_span=cpu_span,
        python_span=python_span,
        generated_at=generated_at,
        table_rows=table_rows,
        histograms_section=histograms_section,
    )


# ---------------------------------------------------------------------------
# JSONL envelope output
# ---------------------------------------------------------------------------

def _emit_jsonl(
    rows: list[dict],
    suite: str,
    run_number: str,
    sha: str,
    out_path: Path,
) -> None:
    """Write one JSONL envelope record per benchmark row to *out_path*.

    Each record uses ``metric = mean_ms`` (mean converted from nanoseconds to
    milliseconds), ``unit = ms``, ``status = pass``.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha12 = sha[:12] if sha else ""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            mean_ms = r["mean"] / 1_000_000  # ns → ms
            rec = {
                "suite": suite,
                "metric": "mean_ms",
                "value": round(mean_ms, 6),
                "unit": "ms",
                "status": "pass",
                "run_number": run_number,
                "sha": sha12,
                "timestamp": timestamp,
            }
            # Embed the benchmark name so readers can distinguish rows
            rec["benchmark"] = r["name"]
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(
        f"generate_benchmark_summary: wrote {len(rows)} JSONL records to {out_path}",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a benchmark summary from pytest-benchmark JSON output.",
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="One or more pytest-benchmark JSON output files to merge "
             "(default: benchmark_results/output_tier0.json)",
        default=["benchmark_results/output_tier0.json"],
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="Produce a self-contained HTML report instead of Markdown.",
    )
    parser.add_argument("--run-id", default="", help="GitHub Actions run ID.")
    parser.add_argument("--run-number", default="?", help="GitHub Actions run number.")
    parser.add_argument("--sha", default="", help="Git commit SHA.")
    parser.add_argument("--ref", default="", help="Git ref name (branch/tag).")
    # Shared-schema envelope flags
    parser.add_argument(
        "--emit-jsonl",
        action="store_true",
        help="Also emit a results.jsonl file in the shared envelope schema.",
    )
    parser.add_argument(
        "--jsonl-out",
        default="",
        help="Output path for results.jsonl (required when --emit-jsonl is set).",
    )
    parser.add_argument(
        "--suite",
        default="tier1-benchmark",
        help="Suite name to embed in JSONL envelope records (e.g. tier1-benchmark).",
    )
    args = parser.parse_args()

    paths = [Path(p) for p in args.inputs]
    existing = [p for p in paths if p.exists()]

    if not existing:
        missing = ", ".join(str(p) for p in paths)
        print(
            f"generate_benchmark_summary: none of [{missing}] found — no results to summarise.",
            file=sys.stderr,
        )
        if args.html:
            print(_render_html({}, [], args.run_id, args.run_number, args.sha, args.ref))
        else:
            print("## Benchmark Results\n\n> No benchmark output file found.")
        sys.exit(0)  # exit 0 so the calling shell step does not fail

    data, rows = _load_merged(paths)

    if args.html:
        print(_render_html(data, rows, args.run_id, args.run_number, args.sha, args.ref))
    else:
        print(_render_markdown(data, rows))

    if args.emit_jsonl:
        if not args.jsonl_out:
            print(
                "generate_benchmark_summary: --emit-jsonl requires --jsonl-out PATH",
                file=sys.stderr,
            )
            sys.exit(1)
        _emit_jsonl(rows, args.suite, args.run_number, args.sha, Path(args.jsonl_out))


if __name__ == "__main__":
    main()

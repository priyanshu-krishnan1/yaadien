"""
scripts/run_benchmarks.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
On-demand entry point for the ``benchmarks/`` harness (retrieval quality,
latency/cost, isolation-under-load). Requires a live Db2 instance; the
retrieval-quality suite additionally uses an ``EmbeddingProvider`` and an
LLM judge (both pluggable — see ``--embedding-provider`` / ``--judge``).

This script is intentionally NOT run by CI (PH-1/PH-2) — see
benchmarks/README.md for why and for free-tier provider setup. Run it
locally via ``make benchmark`` or directly:

    python scripts/run_benchmarks.py
    python scripts/run_benchmarks.py --suite retrieval --dataset-size 10
    python scripts/run_benchmarks.py --suite isolation --tenants 20 --workers 40

By default writes the report to project-management/BENCHMARKS.md.

Exit codes:
    0 — all requested suites ran and (if isolation was included) it passed
        with zero cross-scope leakage.
    1 — configuration/connection error.
    2 — the isolation-under-load suite detected cross-scope leakage
        (a real correctness failure worth a non-zero exit for scripting).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Allow running from the repo root without installing the package, and
# without benchmarks/ or the src/ layout being on sys.path by default.
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is a dev dep; not fatal if missing here

from benchmarks.common.cost_tracking import CostTrackingHook  # noqa: E402
from benchmarks.common.embedding_providers import build_embedding_provider  # noqa: E402
from benchmarks.common.llm_judge import build_judge  # noqa: E402
from benchmarks.common.report import RunMetadata, render_markdown  # noqa: E402
from benchmarks.common.scope_gen import new_run_id  # noqa: E402
from benchmarks.isolation_load.run import run_isolation_load  # noqa: E402
from benchmarks.latency_cost.run import MockConsolidator, run_latency_cost  # noqa: E402
from benchmarks.retrieval_quality.run import run_baseline, run_retrieval_quality  # noqa: E402

from agent_memory_sdk.db.connection import ConnectionError as Db2ConnectionError  # noqa: E402
from agent_memory_sdk.db.connection import ConnectionPool  # noqa: E402
from agent_memory_sdk.db.migrate import Migrator  # noqa: E402
from agent_memory_sdk.store import MemoryStore  # noqa: E402

_DEFAULT_OUTPUT = _REPO_ROOT / "project-management" / "BENCHMARKS.md"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the agent-memory-sdk benchmark harness against a live Db2 instance.",
    )
    parser.add_argument(
        "--suite",
        choices=["all", "retrieval", "latency", "isolation"],
        default="all",
        help="Which suite(s) to run (default: all).",
    )
    parser.add_argument(
        "--embedding-provider",
        choices=["hashing", "sentence-transformers", "ollama"],
        default="hashing",
        help=(
            "Embedding provider used by retrieval/latency/isolation suites. "
            "'hashing' (default) is dependency-free but NOT semantic — see "
            "benchmarks/README.md. Use 'sentence-transformers' (pip install "
            "sentence-transformers) or 'ollama' (local Ollama daemon, "
            "pip install ollama, model nomic-embed-text) for a "
            "retrieval-quality number worth comparing to vendor figures."
        ),
    )
    parser.add_argument(
        "--judge",
        default="keyword",
        help=(
            "Judge used by the retrieval-quality suite. 'keyword' (default) is "
            "a dependency-free fallback heuristic, NOT an LLM judge. Use "
            "'ollama' (local Ollama daemon, pip install ollama, default model "
            "llama3.1:8b) or 'ollama:<model>' (any pulled model, e.g. "
            "'ollama:deepseek-r1:8b') for a LongMemEval-style judge verdict."
        ),
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        default=False,
        help=(
            "Run the no-SDK flat-context baseline alongside the retrieval suite "
            "and include a with-vs-without comparison table in the report. "
            "Only meaningful with --suite retrieval (or all). "
            "Uses the same judge and dataset as the SDK run."
        ),
    )
    parser.add_argument(
        "--dataset-size", type=int, default=4,
        help="Questions generated PER ability category for the retrieval suite (default 4; 5 categories => 5x this many total).",
    )
    parser.add_argument("--seed", type=int, default=42, help="Dataset RNG seed (default 42).")
    parser.add_argument("--top-k", type=int, default=5, help="top_k for retrieval-suite search() calls (default 5).")
    parser.add_argument("--latency-ops", type=int, default=50, help="Number of remember()/search() calls to time (default 50).")
    parser.add_argument(
        "--consolidator", choices=["none", "mock"], default="none",
        help=(
            "'none' (default): NoOp consolidator, $0.00 estimated cost — the "
            "SDK's default write path. 'mock': wires in a non-LLM mock "
            "consolidator (benchmarks/latency_cost/run.py:MockConsolidator) "
            "wrapped in a token-cost estimator, to demonstrate the cost-"
            "tracking mechanism (NOT a real LLM cost claim)."
        ),
    )
    parser.add_argument("--tenants", type=int, default=10, help="Synthetic tenants for the isolation suite (default 10).")
    parser.add_argument("--agents-per-tenant", type=int, default=2, help="Synthetic agents per tenant (default 2).")
    parser.add_argument("--workers", type=int, default=20, help="Concurrent worker threads for the isolation suite (default 20).")
    parser.add_argument("--ops-per-worker", type=int, default=5, help="remember() writes per isolation-suite worker before it reads back (default 5).")
    parser.add_argument("--pool-size", type=int, default=20, help="Db2 ConnectionPool size (default 20 — the SDK's cap).")
    parser.add_argument(
        "--output", type=Path, default=_DEFAULT_OUTPUT,
        help=f"Where to write the markdown report (default {_DEFAULT_OUTPUT.relative_to(_REPO_ROOT)}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    print("agent-memory-sdk — benchmark harness")
    print("=" * 60)

    try:
        pool = ConnectionPool(pool_size=args.pool_size)
    except (OSError, Db2ConnectionError) as exc:
        print(f"Db2 connection error: {exc}", file=sys.stderr)
        return 1

    try:
        Migrator(pool).run()
    except Exception as exc:
        print(f"Migration error: {exc}", file=sys.stderr)
        pool.close()
        return 1

    embedding_dim = 1536
    try:
        embedding_provider = build_embedding_provider(args.embedding_provider, dim=embedding_dim)
    except (ImportError, ValueError) as exc:
        print(f"Embedding provider error: {exc}", file=sys.stderr)
        pool.close()
        return 1

    consolidator_hook = None
    consolidator = None
    if args.consolidator == "mock":
        consolidator_hook = CostTrackingHook(wrapped=MockConsolidator())
        consolidator = consolidator_hook

    store = MemoryStore(pool, embedding_dim=embedding_dim, embedding_provider=embedding_provider, consolidator=consolidator)

    run_id = new_run_id()
    metadata = RunMetadata(
        run_id=run_id,
        embedding_provider=args.embedding_provider,
        embedding_dim=embedding_dim,
    )

    retrieval_result = None
    baseline_result = None
    latency_result = None
    isolation_result = None
    isolation_failed = False

    try:
        if args.suite in ("all", "retrieval"):
            print(f"\n[1/3] Retrieval quality (n_per_category={args.dataset_size}, judge={args.judge})...")
            try:
                judge = build_judge(args.judge)
            except (ImportError, ValueError) as exc:
                print(f"Judge error: {exc}", file=sys.stderr)
                return 1
            retrieval_result = run_retrieval_quality(
                store,
                embedding_provider,
                args.embedding_provider,
                judge,
                args.judge,
                n_per_category=args.dataset_size,
                seed=args.seed,
                top_k=args.top_k,
            )
            print(f"      with-SDK accuracy:    {retrieval_result.overall_accuracy:.1%} "
                  f"({retrieval_result.overall_correct}/{retrieval_result.overall_total})")

            if args.baseline:
                print("      running baseline (no SDK, flat context)...")
                baseline_result = run_baseline(
                    judge,
                    args.judge,
                    n_per_category=args.dataset_size,
                    seed=args.seed,
                )
                delta = retrieval_result.overall_accuracy - baseline_result.overall_accuracy
                print(f"      without-SDK accuracy: {baseline_result.overall_accuracy:.1%} "
                      f"({baseline_result.overall_correct}/{baseline_result.overall_total})  "
                      f"delta: {delta:+.1%}")

        if args.suite in ("all", "latency"):
            print(f"\n[2/3] Latency/cost (n_ops={args.latency_ops}, consolidator={args.consolidator})...")
            latency_result = run_latency_cost(
                store, embedding_provider, n_ops=args.latency_ops, consolidator_hook=consolidator_hook,
            )
            print(f"      remember() p50={latency_result.remember_summary.get('p50_ms')}ms "
                  f"search() p50={latency_result.search_summary.get('p50_ms')}ms")

        if args.suite in ("all", "isolation"):
            print(f"\n[3/3] Isolation under load (tenants={args.tenants} x agents={args.agents_per_tenant}, "
                  f"workers={args.workers})...")
            isolation_result = run_isolation_load(
                store, embedding_provider,
                tenants=args.tenants,
                agents_per_tenant=args.agents_per_tenant,
                concurrent_workers=args.workers,
                ops_per_worker=args.ops_per_worker,
            )
            print(f"      leakage incidents: {isolation_result.leakage_incidents} "
                  f"({'PASS' if isolation_result.passed else 'FAIL'})")
            isolation_failed = not isolation_result.passed
    finally:
        pool.close()

    report_md = render_markdown(
        metadata,
        retrieval=retrieval_result,
        baseline=baseline_result,
        latency=latency_result,
        isolation=isolation_result,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report_md, encoding="utf-8")
    print(f"\nReport written to {args.output}")

    return 2 if isolation_failed else 0


if __name__ == "__main__":
    sys.exit(main())

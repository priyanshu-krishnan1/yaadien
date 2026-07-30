#!/usr/bin/env python3
"""
scripts/consolidate_pending.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Production-grade **async / background consolidation worker** (ENH-4 rewrite).

This script replaces the previous metadata-flag approach (``metadata.consolidated: false``)
with a proper ``consolidated_at`` TIMESTAMP column (migration 0005) and claim-based
locking so that two concurrent worker instances cannot double-process the same row.

Overview
--------
When you don't want the consolidator running inline on the agent's hot path:

1. Leave ``MemoryStore``'s ``consolidator`` as the default
   ``NoOpConsolidator`` so writes are fast.
2. Optionally use ``MemoryStore(consolidate_every_n=N)`` to throttle inline
   consolidation to every Nth write per scope — further reducing LLM-call cost.
3. Run this script periodically (cron / Kubernetes CronJob / background
   thread) to pick up unconsolidated rows and run the consolidator off the
   hot path.

Eligibility filter
------------------
Rows are eligible when ``consolidated_at IS NULL``.  This is a first-class
indexed column (``ix_working_memory_consolidated_at``,
``ix_episodic_memory_consolidated_at``) added by migration 0005, replacing
the previous JSON-flag approach::

    # Old (stand-in — don't use):
    AND JSON_VALUE(metadata, '$.consolidated') = 'false'

    # New (production):
    AND consolidated_at IS NULL

Claim-based locking
-------------------
Before processing a row the worker issues::

    UPDATE <table>
    SET consolidated_at = <now>
    WHERE id = ? AND <scope> AND consolidated_at IS NULL

If the rowcount is 0 (another worker already claimed the row), the row is
skipped silently.  If rowcount is 1 the worker owns the row and proceeds to
run the consolidator and persist derived memories.

This prevents double-processing under concurrent workers without requiring
``SELECT FOR UPDATE``, distributed locks, or any new infrastructure — the
approach is idempotent at the row level using Db2's built-in row-level
locking to serialize the competing UPDATEs.

``--dedup-every-n`` — optional Reconciler cadence
--------------------------------------------------
The worker can optionally run the ENH-3 :class:`~agent_memory_sdk.types.Reconciler`
every *N* completed batches to detect and soft-supersede contradictory
semantic facts.  This mirrors the Cosmos DB Agent Memory Toolkit's
``DEDUP_EVERY_N`` pattern::

    python scripts/consolidate_pending.py \\
        --agent-id agent-001 \\
        --consolidator-module myapp.consolidators \\
        --consolidator-class LLMConsolidator \\
        --reconciler-module myapp.reconcilers \\
        --reconciler-class LLMReconciler \\
        --dedup-every-n 2   # run reconciler after every 2nd batch (maximum supported)

**Hard limit — ``--dedup-every-n`` must be 1 or 2.**

Each script invocation processes exactly two memory types (``working`` and
``episodic``), so ``batches_completed`` starts at 0 and can reach at most 2
per run.  A value of N >= 3 would never satisfy
``batches_completed % N == 0`` within a single invocation and would silently
do nothing — an argument value that appears valid but is permanently a no-op.

To avoid that silent-no-op footgun, the script rejects ``--dedup-every-n``
values > 2 at argument-parsing time with a clear error.  If you need
deduplication on a longer cadence (e.g. every 10th run), schedule the
Reconciler in a separate cron job rather than relying on this per-invocation
counter.

Db2 substitute for Cosmos change-feed
--------------------------------------
Cosmos DB's Agent Memory Toolkit uses change-feed-triggered Azure Durable
Functions to process memories off the hot write path asynchronously.  Db2
LUW has no native change-feed mechanism.  This polling worker is the
Db2-appropriate substitute — same goal (async, off-the-hot-path
processing), different mechanism.  It requires no new external service
dependency, keeping the Step 0 "zero mandatory external services" principle
intact.  See DECISIONS.md ENH-4 entry.

Usage::

    python scripts/consolidate_pending.py \\
        --agent-id <agent_id> \\
        --batch-size 20

Environment
-----------
Same ``DB2_*`` variables as ``purge_expired.py``; also reads ``OPENAI_API_KEY``
if you use the LLM-based consolidator example from
:class:`~agent_memory_sdk.types.Consolidator`.

The consolidator class is passed in via ``--consolidator-module`` and
``--consolidator-class``::

    python scripts/consolidate_pending.py \\
        --agent-id agent-001 \\
        --consolidator-module myapp.consolidators \\
        --consolidator-class LLMConsolidator
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _build_pool():
    from agent_memory_sdk.db.connection import ConnectionPool
    return ConnectionPool(
        database=os.environ["DB2_DATABASE"],
        hostname=os.environ["DB2_HOSTNAME"],
        port=int(os.environ.get("DB2_PORT", 50000)),
        uid=os.environ["DB2_UID"],
        pwd=os.environ["DB2_PWD"],
        security=os.environ.get("DB2_SECURITY", ""),
        pool_size=1,
    )


def _load_class(module_path: str, class_name: str):
    """Import and instantiate a class from a dotted module path."""
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls()


def _fetch_pending(repo, scope, batch_size: int) -> list:
    """Return up to *batch_size* non-deleted, unconsolidated rows.

    Uses ``consolidated_at IS NULL`` as the eligibility filter (migration
    0005 column), replacing the previous ``JSON_VALUE(metadata, '$.consolidated')
    = 'false'`` approach that the original docstring flagged as a stand-in.
    """
    from agent_memory_sdk.repositories.base import _require_agent_id, _scope_predicates
    _require_agent_id(scope)
    scope_sql, scope_params = _scope_predicates(scope)

    sql = (
        f"SELECT {repo._SELECT_COLS} FROM {repo._TABLE} "
        f"WHERE {scope_sql} "
        f"  AND deleted_at IS NULL "
        f"  AND consolidated_at IS NULL "
        f"FETCH FIRST {batch_size} ROWS ONLY"
    )
    with repo._pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, scope_params)
        rows = cur.fetchall()
    return [repo._model_from_row(r) for r in rows]


def _process_record(repo, store, consolidator, scope, record) -> bool:
    """Claim and process one record.

    1. Attempt to claim the row via ``_claim_consolidated()`` (claim-based
       locking — prevents two workers from double-processing the same row).
    2. If claim fails (rowcount == 0), another worker beat us here — skip.
    3. If claim succeeds, run the consolidator and persist derived memories.

    Returns:
        True  — row was claimed and processed by this worker.
        False — row was already claimed by another worker; skipped.
    """
    claimed = repo._claim_consolidated(record.id, scope)
    if not claimed:
        print(f"  SKIP {record.id} — claimed by another worker")
        return False

    derived = consolidator([record])
    for dr in derived:
        dr_repo_attr = {
            "SemanticFact": "facts",
            "EntityProfile": "profiles",
            "ProceduralMemory": "procedures",
        }.get(type(dr).__name__)
        if dr_repo_attr:
            getattr(store, dr_repo_attr).create(dr, scope)
            print(f"  + {type(dr).__name__} id={dr.id}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run consolidation on pending (unconsolidated) memories."
    )
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--tenant-id", default=None)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument(
        "--memory-types",
        default="working,episodic",
        help="Comma-separated list of memory types to process (default: working,episodic)",
    )
    parser.add_argument(
        "--consolidator-module",
        default=None,
        help="Dotted module path for the consolidator class (e.g. myapp.consolidators)",
    )
    parser.add_argument(
        "--consolidator-class",
        default=None,
        help="Class name inside the module (e.g. LLMConsolidator)",
    )
    parser.add_argument(
        "--reconciler-module",
        default=None,
        help="Dotted module path for the reconciler class (e.g. myapp.reconcilers). "
             "Requires --dedup-every-n.",
    )
    parser.add_argument(
        "--reconciler-class",
        default=None,
        help="Class name for the reconciler (e.g. LLMReconciler).",
    )
    parser.add_argument(
        "--dedup-every-n",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Run the Reconciler (ENH-3 dedup) every N completed batches. "
            "0 = disabled (default).  Requires --reconciler-module/class.  "
            "Must be 1 or 2: each invocation processes exactly two memory "
            "types (working + episodic), so batches_completed can reach at "
            "most 2 per run.  N >= 3 can never trigger within a single run "
            "and is rejected to prevent a silent no-op."
        ),
    )
    args = parser.parse_args()

    if args.dedup_every_n > 2:
        parser.error(
            f"--dedup-every-n must be 1 or 2 (got {args.dedup_every_n}).  "
            "Each script invocation processes exactly two memory types "
            "(working + episodic), so the per-invocation batch counter "
            "reaches at most 2.  A value of N >= 3 can never satisfy "
            "batches_completed % N == 0 within a single run and would "
            "silently do nothing.  To run the Reconciler on a longer "
            "cadence, schedule a separate cron job instead."
        )

    try:
        from dotenv import load_dotenv  # type: ignore[import]
        load_dotenv()
    except ImportError:
        pass

    from agent_memory_sdk.models import MemoryScope
    from agent_memory_sdk.store import MemoryStore
    from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler

    scope = MemoryScope(agent_id=args.agent_id, tenant_id=args.tenant_id)

    if args.consolidator_module and args.consolidator_class:
        consolidator = _load_class(args.consolidator_module, args.consolidator_class)
        print(f"Using consolidator: {args.consolidator_module}.{args.consolidator_class}")
    else:
        consolidator = NoOpConsolidator()
        print("No consolidator specified — using NoOpConsolidator (nothing will be derived).")

    # Reconciler for --dedup-every-n batches
    reconciler = None
    if args.dedup_every_n > 0:
        if args.reconciler_module and args.reconciler_class:
            reconciler = _load_class(args.reconciler_module, args.reconciler_class)
            print(
                f"Reconciler: {args.reconciler_module}.{args.reconciler_class} "
                f"(every {args.dedup_every_n} batches)"
            )
        else:
            print(
                "WARNING: --dedup-every-n requires --reconciler-module and "
                "--reconciler-class; dedup disabled."
            )
            args.dedup_every_n = 0

    pool = _build_pool()
    store = MemoryStore(pool, reconciler=reconciler or NoOpReconciler())

    type_to_repo = {
        "working": store.working,
        "episodic": store.episodic,
    }

    batches_completed = 0

    for mem_type in args.memory_types.split(","):
        mem_type = mem_type.strip()
        repo = type_to_repo.get(mem_type)
        if repo is None:
            print(f"  Skipping unknown memory type: {mem_type!r}")
            continue

        records = _fetch_pending(repo, scope, args.batch_size)
        print(f"{mem_type}: {len(records)} pending rows")

        processed = 0
        for record in records:
            if _process_record(repo, store, consolidator, scope, record):
                processed += 1

        print(f"{mem_type}: {processed} rows processed (claimed + consolidated)")
        batches_completed += 1

        # --dedup-every-n: run the Reconciler every N completed batches.
        if args.dedup_every_n > 0 and batches_completed % args.dedup_every_n == 0:
            print(f"Running Reconciler (batch {batches_completed}, every {args.dedup_every_n})…")
            try:
                applied = store.reconcile("facts", scope)
                print(f"  Reconciler applied {len(applied)} supersession decision(s).")
            except Exception as exc:
                print(f"  WARNING: Reconciler raised: {exc}")

    print("Done.")


if __name__ == "__main__":
    main()

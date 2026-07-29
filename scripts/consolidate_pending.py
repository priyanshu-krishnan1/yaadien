#!/usr/bin/env python3
"""
scripts/consolidate_pending.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Reference implementation of the **async / background consolidation** pattern.

Overview
--------
When you don't want the consolidator running inline on the agent's hot path,
you can:

1. Leave ``MemoryStore``'s ``consolidator`` as the default
   ``NoOpConsolidator`` so writes are fast.
2. Mark each raw memory row as "pending consolidation" at write time by
   including a flag in the record's ``metadata``::

       store.working.create(
           WorkingMemory(
               agent_id="agent-001",
               content=turn_text,
               metadata={"consolidated": False},
           ),
           scope,
       )

3. Run this script periodically (cron / Kubernetes CronJob / background
   thread) to pick up unprocessed rows, run the consolidator, persist the
   derived memories, and mark the source rows as processed.

This script is intentionally simple — it is a *reference* you should copy
and adapt, not a production-grade worker (no locking, no idempotency keys,
no parallelism).  A real implementation would add a ``consolidated_at``
TIMESTAMP column to the schema (a new migration) and use
``WHERE consolidated_at IS NULL`` as the eligibility filter.

Usage::

    python scripts/consolidate_pending.py \\
        --agent-id <agent_id> \\
        --batch-size 20

Environment
-----------
Same DB2_* variables as ``purge_expired.py``; also reads ``OPENAI_API_KEY``
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


def _load_consolidator(module_path: str, class_name: str):
    """Import and instantiate the consolidator class from a dotted module path."""
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls()


def _fetch_pending(repo, scope, batch_size: int) -> list:
    """Return up to *batch_size* non-deleted, non-consolidated rows."""
    from agent_memory_sdk.repositories.base import _require_agent_id, _scope_predicates
    _require_agent_id(scope)
    scope_sql, scope_params = _scope_predicates(scope)

    # Filter: rows where metadata JSON contains "consolidated": false
    # JSON_VALUE is supported in Db2 12.1; adapt if using an older version.
    sql = (
        f"SELECT {repo._SELECT_COLS} FROM {repo._TABLE} "
        f"WHERE {scope_sql} "
        f"  AND deleted_at IS NULL "
        f"  AND JSON_VALUE(metadata, '$.consolidated') = 'false' "
        f"FETCH FIRST {batch_size} ROWS ONLY"
    )
    with repo._pool.get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, scope_params)
        rows = cur.fetchall()
    return [repo._model_from_row(r) for r in rows]


def _mark_consolidated(repo, scope, record) -> None:
    """Set metadata.consolidated = true for a processed row."""
    record.metadata["consolidated"] = True
    try:
        repo.update(record, scope)
    except Exception as exc:
        print(f"  WARNING: Could not mark {record.id} as consolidated: {exc}")


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
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv  # type: ignore[import]
        load_dotenv()
    except ImportError:
        pass

    from agent_memory_sdk.models import MemoryScope
    from agent_memory_sdk.store import MemoryStore
    from agent_memory_sdk.types import NoOpConsolidator

    scope = MemoryScope(agent_id=args.agent_id, tenant_id=args.tenant_id)

    if args.consolidator_module and args.consolidator_class:
        consolidator = _load_consolidator(args.consolidator_module, args.consolidator_class)
        print(f"Using consolidator: {args.consolidator_module}.{args.consolidator_class}")
    else:
        consolidator = NoOpConsolidator()
        print("No consolidator specified — using NoOpConsolidator (nothing will be derived).")

    pool = _build_pool()
    store = MemoryStore(pool)

    type_to_repo = {
        "working": store.working,
        "episodic": store.episodic,
    }

    for mem_type in args.memory_types.split(","):
        mem_type = mem_type.strip()
        repo = type_to_repo.get(mem_type)
        if repo is None:
            print(f"  Skipping unknown memory type: {mem_type!r}")
            continue

        records = _fetch_pending(repo, scope, args.batch_size)
        print(f"{mem_type}: {len(records)} pending rows")

        for record in records:
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

            _mark_consolidated(repo, scope, record)

    print("Done.")


if __name__ == "__main__":
    main()

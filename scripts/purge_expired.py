#!/usr/bin/env python3
"""
scripts/purge_expired.py
~~~~~~~~~~~~~~~~~~~~~~~~
Maintenance script: hard-delete tombstoned rows from all five memory tables.

Usage::

    python scripts/purge_expired.py --agent-id <agent_id> [--tenant-id <tenant_id>]
    python scripts/purge_expired.py --agent-id <agent_id> --dry-run

This is the cron-job / ops-tool entry point for the lifecycle purge path.
The SDK's ``purge_expired()`` is never called automatically — this script
must be scheduled explicitly (e.g. daily via cron or a Kubernetes CronJob).

What gets deleted
-----------------
Any row where ``deleted_at IS NOT NULL`` (i.e. the row has already been
tombstoned by a ``store.forget()`` or ``repo.forget()`` call) within the
supplied scope.  Rows with an ``expires_at`` in the past but NOT yet
tombstoned are left untouched — the normal query path already excludes them
from results, and they are only eligible for hard-delete once explicitly
tombstoned.

Scope
-----
A scope (at minimum ``agent_id``) MUST be provided so that this script
never accidentally touches another agent's or tenant's data.  To purge
across multiple agents/tenants, run the script once per scope.

Environment
-----------
Connection parameters are read from environment variables (or a .env file):

    DB2_DATABASE, DB2_HOSTNAME, DB2_PORT, DB2_UID, DB2_PWD, DB2_SECURITY
    DB2_POOL_SIZE (optional, default 1 — only one connection needed here)

See .env.example for details.
"""

from __future__ import annotations

import argparse
import os
import sys

# Make the package importable when run from the repo root without installing.
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hard-delete tombstoned rows from all five memory tables."
    )
    parser.add_argument("--agent-id", required=True, help="agent_id scope (required)")
    parser.add_argument("--tenant-id", default=None, help="tenant_id scope (optional)")
    parser.add_argument("--user-id", default=None, help="user_id scope (optional)")
    parser.add_argument("--thread-id", default=None, help="thread_id scope (optional)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print how many rows would be deleted without deleting anything.",
    )
    args = parser.parse_args()

    # Load .env if present (best-effort; skip if python-dotenv isn't installed)
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        load_dotenv()
    except ImportError:
        pass

    from agent_memory_sdk.models import MemoryScope
    from agent_memory_sdk.store import MemoryStore

    scope = MemoryScope(
        agent_id=args.agent_id,
        tenant_id=args.tenant_id,
        user_id=args.user_id,
        thread_id=args.thread_id,
    )

    if args.dry_run:
        # Count tombstoned rows per table without deleting.
        pool = _build_pool()
        store = MemoryStore(pool)
        print("[dry-run] Would purge tombstoned rows in scope:", scope)
        for repo in (
            store.working,
            store.episodic,
            store.facts,
            store.profiles,
            store.procedures,
        ):
            from agent_memory_sdk.repositories.base import _require_agent_id, _scope_predicates
            _require_agent_id(scope)
            scope_sql, scope_params = _scope_predicates(scope)
            sql = (
                f"SELECT COUNT(*) FROM {repo._TABLE} "
                f"WHERE deleted_at IS NOT NULL AND {scope_sql}"
            )
            with pool.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(sql, scope_params)
                row = cur.fetchone()
                count = row[0] if row else 0
            print(f"  {repo._TABLE}: {count} rows eligible")
        return

    pool = _build_pool()
    store = MemoryStore(pool)
    results = store.purge_expired(scope)
    total = sum(results.values())
    for table, count in results.items():
        print(f"  {table}: {count} rows deleted")
    print(f"Total: {total} rows purged.")


if __name__ == "__main__":
    main()

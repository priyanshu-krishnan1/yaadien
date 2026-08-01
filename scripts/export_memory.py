#!/usr/bin/env python3
"""
scripts/export_memory.py
~~~~~~~~~~~~~~~~~~~~~~~~
Export a tenant/agent's memory to a portable JSONL file.

Usage::

    python scripts/export_memory.py --agent-id AGENT_ID [--tenant-id T]
        [--user-id U] [--thread-id TH] --output PATH.jsonl

Each line in the output file is a JSON object with a ``_type`` discriminator
field (one of ``working_memory``, ``episodic_memory``, ``semantic_facts``,
``entity_profiles``, ``procedural_memory``, ``memory_chunks``) plus all
fields from the corresponding Pydantic model, with datetime fields serialized
as ISO-8601 strings and ``embedding`` as a raw list of floats.

**This is this SDK's own proprietary backup/portability format — not a
cross-vendor interchange standard.** No such standard exists industry-wide;
see ``project-management/ai-agent-platform-competitive-analysis.md`` gap
analysis #3 and DECISIONS.md for the full rationale.

Environment
-----------
Connection parameters are read from environment variables (or a .env file):

    DB2_DATABASE, DB2_HOSTNAME, DB2_PORT, DB2_UID, DB2_PWD, DB2_SECURITY

See .env.example for details.
"""

from __future__ import annotations

import argparse
import json
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
        description="Export a tenant/agent's memory to a JSONL file (proprietary format)."
    )
    parser.add_argument("--agent-id", required=True, help="agent_id scope (required)")
    parser.add_argument("--tenant-id", default=None, help="tenant_id scope (optional)")
    parser.add_argument("--user-id", default=None, help="user_id scope (optional)")
    parser.add_argument("--thread-id", default=None, help="thread_id scope (optional)")
    parser.add_argument("--output", required=True, help="Output JSONL file path")
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

    pool = _build_pool()
    store = MemoryStore(pool)

    count = 0
    with open(args.output, "w", encoding="utf-8") as fh:
        for record in store.export_scope(scope):
            fh.write(json.dumps(record, default=str) + "\n")
            count += 1

    print(f"Exported {count} records to {args.output}")


if __name__ == "__main__":
    main()

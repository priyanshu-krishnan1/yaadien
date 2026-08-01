#!/usr/bin/env python3
"""
scripts/import_memory.py
~~~~~~~~~~~~~~~~~~~~~~~~
Import a tenant/agent's memory from a JSONL file produced by
``scripts/export_memory.py``.

Usage::

    python scripts/import_memory.py --agent-id AGENT_ID [--tenant-id T]
        [--user-id U] [--thread-id TH] --input PATH.jsonl

Reads one JSON object per line from PATH.jsonl, calls
``MemoryStore.import_scope()``, and prints a per-type summary.

Each record must carry a ``_type`` discriminator field matching one of:
``working_memory``, ``episodic_memory``, ``semantic_facts``,
``entity_profiles``, ``procedural_memory``, ``memory_chunks``.

``memory_chunks`` records are skipped silently — chunk rows are regenerated
automatically by ``create()`` when ``enable_chunking=True`` is configured on
the target store, so importing the raw chunk rows from the export file is
neither necessary nor correct (the source IDs would not match the new rows).

**Scope validation:** every record's stored ``agent_id`` (and ``tenant_id``
when both are non-None) must exactly match the target scope provided on the
command line. A mismatch raises ``ScopeImportError`` and the import stops.
To import records spanning multiple scopes, run this script once per scope.

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
        description="Import a tenant/agent's memory from a JSONL file."
    )
    parser.add_argument("--agent-id", required=True, help="agent_id scope (required)")
    parser.add_argument("--tenant-id", default=None, help="tenant_id scope (optional)")
    parser.add_argument("--user-id", default=None, help="user_id scope (optional)")
    parser.add_argument("--thread-id", default=None, help="thread_id scope (optional)")
    parser.add_argument("--input", required=True, help="Input JSONL file path")
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

    def _records():
        with open(args.input, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    counts = store.import_scope(_records(), scope)

    total = sum(counts.values())
    for type_name, count in counts.items():
        print(f"  {type_name}: {count} records imported")
    print(f"Total: {total} records imported from {args.input}")


if __name__ == "__main__":
    main()

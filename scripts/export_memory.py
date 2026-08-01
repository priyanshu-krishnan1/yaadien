#!/usr/bin/env python3
"""
scripts/export_memory.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Reference CLI: export a tenant/agent's memory to a JSONL backup file (PIPE-6).

Usage::

    python scripts/export_memory.py --agent-id <agent_id> --output backup.jsonl
    python scripts/export_memory.py --agent-id <agent_id> --tenant-id <tenant_id> \\
        --user-id <user_id> --output backup.jsonl
    python scripts/export_memory.py --agent-id <agent_id> --output backup.jsonl --no-chunks

What this writes
-----------------
One JSON object per line (JSONL), each tagged with a ``"_type"``
discriminator field naming its source table: ``"working_memory"``,
``"episodic_memory"``, ``"semantic_facts"``, ``"entity_profiles"``,
``"procedural_memory"``, or ``"memory_chunks"``. This is produced directly
by ``MemoryStore.export_scope()`` — see that method's docstring in
``src/agent_memory_sdk/store.py`` for the exact field-by-field shape.

**This is this SDK's own proprietary backup format, not a cross-vendor
interchange standard** — no such standard exists anywhere in the industry
(see ``project-management/ai-agent-platform-competitive-analysis.md`` gap
analysis #3: even vendors that advertise "import/export" support, such as
Mem0 and Oracle, each use a format proprietary to that vendor). Embedding
vectors are written as raw JSON float lists, exactly as this SDK represents
them internally in Python — there is no additional portable encoding
applied. Use ``scripts/import_memory.py`` (or ``MemoryStore.import_scope()``
directly) to restore a file produced by this script.

Scope
-----
A scope (at minimum ``agent_id``) MUST be provided, same as
``scripts/purge_expired.py``. Only rows visible to that exact scope are
exported. To export multiple agents/tenants/users, run this script once per
scope.

What gets included
-------------------
The same rows ``list_all()`` would return for the scope — non-deleted and
(for ``semantic_facts``) non-superseded rows, including TTL-expired-but-not-
yet-tombstoned rows. Tombstoned and superseded rows are intentionally
excluded — see ``MemoryStore.export_scope()``'s docstring for the full
reasoning.

Environment
-----------
Same ``DB2_*`` variables as ``purge_expired.py`` / ``consolidate_pending.py``:

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
        description="Export a tenant/agent's memory to a JSONL backup file."
    )
    parser.add_argument("--agent-id", required=True, help="agent_id scope (required)")
    parser.add_argument("--tenant-id", default=None, help="tenant_id scope (optional)")
    parser.add_argument("--user-id", default=None, help="user_id scope (optional)")
    parser.add_argument("--thread-id", default=None, help="thread_id scope (optional)")
    parser.add_argument(
        "--output", required=True, help="Path to the JSONL file to write."
    )
    parser.add_argument(
        "--no-chunks",
        action="store_true",
        help="Skip memory_chunks rows even if this schema has chunked content.",
    )
    parser.add_argument(
        "--embedding-dim",
        type=int,
        default=1536,
        help="Vector dimension, must match the schema (default 1536).",
    )
    args = parser.parse_args()

    # Load .env if present (best-effort; skip if python-dotenv isn't installed)
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        load_dotenv()
    except ImportError:
        pass

    from agent_memory_sdk.models import MemoryScope
    from agent_memory_sdk.repositories.chunks import ChunkRepository
    from agent_memory_sdk.store import MemoryStore

    scope = MemoryScope(
        agent_id=args.agent_id,
        tenant_id=args.tenant_id,
        user_id=args.user_id,
        thread_id=args.thread_id,
    )

    pool = _build_pool()
    store = MemoryStore(pool, embedding_dim=args.embedding_dim)
    if not args.no_chunks:
        # export_scope() only *reads* memory_chunks via ChunkRepository.list_all(),
        # so no embedding_provider is required here — MemoryStore's constructor
        # only wires up a ChunkRepository automatically when an embedding_provider
        # is supplied (ORC-2's write-path gating). Attach one directly so the
        # read-only export path can see memory_chunks rows regardless.
        store.chunks = ChunkRepository(pool, embedding_dim=args.embedding_dim)

    counts: dict[str, int] = {}
    with open(args.output, "w", encoding="utf-8") as f:
        for record in store.export_scope(scope):
            f.write(json.dumps(record) + "\n")
            counts[record["_type"]] = counts.get(record["_type"], 0) + 1

    total = sum(counts.values())
    if not counts:
        print("No rows matched this scope — nothing exported.")
    for type_name, count in sorted(counts.items()):
        print(f"  {type_name}: {count} rows exported")
    print(f"Total: {total} rows written to {args.output}")


if __name__ == "__main__":
    main()

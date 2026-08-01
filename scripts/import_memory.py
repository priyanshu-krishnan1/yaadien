#!/usr/bin/env python3
"""
scripts/import_memory.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Reference CLI: restore a JSONL backup file produced by ``scripts/export_memory.py``
(PIPE-6) into a ``MemoryStore`` scope.

Usage::

    python scripts/import_memory.py --agent-id <agent_id> --input backup.jsonl
    python scripts/import_memory.py --agent-id <agent_id> --tenant-id <tenant_id> \\
        --user-id <user_id> --input backup.jsonl

Scope re-validation
--------------------
Every record in the file must carry scope columns (``tenant_id``/``agent_id``/
``user_id``/``thread_id``) matching the ``--agent-id``/``--tenant-id``/
``--user-id``/``--thread-id`` given on the command line, exactly as recorded
when it was exported. ``MemoryStore.import_scope()`` checks this per record
and raises ``ScopeMismatchError`` (a clear, immediate failure) rather than
silently rewriting a record into the wrong scope — this is a deliberate
safety property, not a bug. To restore a file spanning multiple scopes, run
this script once per distinct scope (or pre-filter the JSONL file by scope
before running it).

This is this SDK's own proprietary format (see ``export_memory.py``'s
docstring) — not a cross-vendor interchange standard. No such standard
exists anywhere in the industry (see
``project-management/ai-agent-platform-competitive-analysis.md`` gap
analysis #3).

Known limitation (inherited from ``create()``)
-----------------------------------------------
Each record is re-inserted via the ordinary per-type ``create()`` path, so
the usual write-time dedup check (ENH-2) still applies for
``semantic_facts``/``entity_profiles``/``procedural_memory``: if a row with
the same ``(scope, content_hash)`` already exists live in the target scope,
``create()`` returns that existing row instead of inserting a duplicate.
``created_at``/``updated_at``/``version`` are also reset to "now" / ``1`` —
an import produces fresh live rows, not a byte-for-byte replica of the
original row's lifecycle timestamps. ``working_memory`` has no dedup gate,
so its rows always re-insert faithfully.

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
from collections.abc import Iterator
from typing import Any

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


def _read_jsonl(path: str) -> Iterator[dict[str, Any]]:
    """Yield one dict per non-blank line of a JSONL file, in order.

    Raises ``ValueError`` (with file + line number) on malformed JSON so a
    corrupt backup file fails fast with an actionable message rather than a
    bare ``json.JSONDecodeError``.
    """
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a JSONL memory backup file into a MemoryStore scope."
    )
    parser.add_argument("--agent-id", required=True, help="agent_id scope (required)")
    parser.add_argument("--tenant-id", default=None, help="tenant_id scope (optional)")
    parser.add_argument("--user-id", default=None, help="user_id scope (optional)")
    parser.add_argument("--thread-id", default=None, help="thread_id scope (optional)")
    parser.add_argument(
        "--input", required=True, help="Path to the JSONL file to read."
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
    # import_scope() only *writes* pre-computed vectors via insert_chunk() for
    # memory_chunks rows — it never calls an embedding provider — so attach a
    # ChunkRepository directly (same reasoning as export_memory.py) so a file
    # containing memory_chunks rows can be restored even without one configured.
    store.chunks = ChunkRepository(pool, embedding_dim=args.embedding_dim)

    counts = store.import_scope(_read_jsonl(args.input), scope)

    total = sum(counts.values())
    for type_name, count in sorted(counts.items()):
        print(f"  {type_name}: {count} rows imported")
    print(f"Total: {total} rows imported from {args.input}")


if __name__ == "__main__":
    main()

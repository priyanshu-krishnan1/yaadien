"""
scripts/check_connection.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Verify Db2 connectivity by opening a pooled connection and running a trivial
query against SYSIBM.SYSDUMMY1 (Db2's equivalent of Oracle's DUAL).

Usage:
    # From the repo root, after copying .env.example to .env and filling it in:
    python scripts/check_connection.py

    # Or with env vars set explicitly:
    DB2_DATABASE=MYDB DB2_HOSTNAME=localhost DB2_UID=db2inst1 DB2_PWD=secret \
        python scripts/check_connection.py

Exit codes:
    0 — connection succeeded and query returned the expected result
    1 — connection or query failed (error printed to stderr)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Load .env if present (dev convenience; not required in production).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is a dev dep; not fatal if missing here

from agent_memory_sdk.db.connection import ConnectionError, ConnectionPool, ConnectionPoolExhausted


def main() -> int:
    print("agent-memory-sdk — Db2 connectivity check")
    print("=" * 50)

    try:
        pool = ConnectionPool(pool_size=1)
    except OSError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except ConnectionError as exc:
        print(f"Connection failed: {exc}", file=sys.stderr)
        return 1

    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
            row = cur.fetchone()
    except (ConnectionPoolExhausted, ConnectionError, Exception) as exc:
        print(f"Query failed: {exc}", file=sys.stderr)
        pool.close()
        return 1
    finally:
        pool.close()

    if row and row[0] == 1:
        print("✓ Connection successful — SYSIBM.SYSDUMMY1 returned 1")
        return 0
    else:
        print(f"✗ Unexpected query result: {row!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

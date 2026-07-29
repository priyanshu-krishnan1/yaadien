"""
db/migrate.py
~~~~~~~~~~~~~
Minimal SQL migration runner for agent-memory-sdk.

Design decisions:
- No alembic: ibm_db_dbi/Db2 support in alembic is inconsistent and would
  add a heavyweight dependency. This runner is ~100 lines of stdlib code.
- Migration files live in src/agent_memory_sdk/db/migrations/ and are named
  <NNNN>_<description>.sql where NNNN is a zero-padded integer. Files are
  applied in lexicographic (i.e. numeric) order.
- Applied versions are tracked in a schema_migrations table (created on
  first run if it does not exist).
- Each .sql file may contain multiple statements separated by semicolons.
  Blank lines and SQL comments (-- ...) are preserved but not executed as
  separate statements.
- Migrations are NOT transactional at the DDL level in Db2 (DDL is
  auto-committed). If a migration file fails mid-way, the already-applied
  statements within it cannot be rolled back. The version record is only
  inserted after ALL statements in the file succeed, so a partial failure
  leaves the version unrecorded — re-running migrate() will retry the whole
  file. Design DDL files to be idempotent where possible (e.g. use
  CREATE TABLE IF NOT EXISTS for the tracking table itself).

Usage (from repo root)::

    # Apply all pending migrations:
    python -m agent_memory_sdk.db.migrate

    # Or from a script:
    from agent_memory_sdk.db.connection import ConnectionPool
    from agent_memory_sdk.db.migrate import Migrator
    pool = ConnectionPool()
    Migrator(pool).run()
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_memory_sdk.db.connection import ConnectionPool

logger = logging.getLogger(__name__)

# Directory containing .sql migration files (co-located with this module)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Bootstrap DDL to create the tracking table if it doesn't already exist.
# Db2 supports CREATE TABLE IF NOT EXISTS since 11.1.
_BOOTSTRAP_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP    NOT NULL DEFAULT CURRENT TIMESTAMP,
    CONSTRAINT pk_schema_migrations PRIMARY KEY (version)
)
"""


def _split_statements(sql: str) -> list[str]:
    """Split a SQL file into individual statements on semicolons.

    Strips SQL line comments (-- ...) and returns only non-empty statements.
    Does not handle block comments (/* ... */); avoid them in migration files.
    """
    # Remove single-line comments
    sql_no_comments = re.sub(r"--[^\n]*", "", sql)
    raw = sql_no_comments.split(";")
    return [s.strip() for s in raw if s.strip()]


class MigrationError(RuntimeError):
    """Raised when a migration statement fails."""


class Migrator:
    """Apply pending .sql migrations against a ConnectionPool.

    Args:
        pool: An open :class:`~agent_memory_sdk.db.connection.ConnectionPool`.
        migrations_dir: Override the default migrations directory (for testing).
    """

    def __init__(self, pool: ConnectionPool, migrations_dir: Path | None = None) -> None:
        self._pool = pool
        self._dir = migrations_dir or MIGRATIONS_DIR

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> list[str]:
        """Apply all pending migrations in order.

        Returns:
            List of version strings that were applied in this run.
        """
        self._bootstrap()
        applied = self._applied_versions()
        pending = self._pending_files(applied)

        if not pending:
            logger.info("No pending migrations.")
            return []

        newly_applied: list[str] = []
        for path in pending:
            version = path.stem  # e.g. "0001_schema_migrations"
            logger.info("Applying migration: %s", version)
            self._apply_file(path, version)
            newly_applied.append(version)
            logger.info("Migration applied: %s", version)

        return newly_applied

    def status(self) -> dict[str, str]:
        """Return a mapping of {version: 'applied'|'pending'} for all files."""
        try:
            self._bootstrap()
            applied = self._applied_versions()
        except Exception:
            applied = set()

        result: dict[str, str] = {}
        for path in sorted(self._dir.glob("*.sql")):
            v = path.stem
            result[v] = "applied" if v in applied else "pending"
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bootstrap(self) -> None:
        """Ensure the schema_migrations table exists.

        Strategy: check the catalog first; create the table only if absent.
        This avoids executing Db2-specific DDL against other DB-API backends
        (e.g. SQLite used in unit tests) and removes the need to suppress
        "table already exists" errors.
        """
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            # Ask the DB-API cursor whether the table exists.  We query a row
            # count; an exception means the table doesn't exist yet.
            try:
                cur.execute("SELECT COUNT(*) FROM schema_migrations")
                cur.fetchone()
                return  # table already exists — nothing to do
            except Exception:
                pass  # table is absent; fall through to create it

            # Create the tracking table using Db2 DDL.
            cur.execute(_BOOTSTRAP_DDL.strip())
            conn.commit()

    def _applied_versions(self) -> set[str]:
        """Return the set of already-applied migration version strings."""
        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            rows = cur.fetchall()
        return {row[0] for row in rows}

    def _pending_files(self, applied: set[str]) -> list[Path]:
        """Return sorted list of .sql files not yet in applied."""
        all_files = sorted(self._dir.glob("*.sql"))
        return [f for f in all_files if f.stem not in applied]

    def _apply_file(self, path: Path, version: str) -> None:
        """Execute all statements in a .sql file, then record the version."""
        sql = path.read_text(encoding="utf-8")
        statements = _split_statements(sql)

        with self._pool.get_connection() as conn:
            cur = conn.cursor()
            for stmt in statements:
                logger.debug("Executing: %s", stmt[:80])
                try:
                    cur.execute(stmt)
                except Exception as exc:
                    raise MigrationError(
                        f"Migration {version} failed on statement:\n{stmt}\nError: {exc}"
                    ) from exc

            # Record the version only after all statements succeed
            cur.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (version,),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    # Import here so the module is usable without ibm_db installed (tests mock it)
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    from agent_memory_sdk.db.connection import ConnectionPool

    try:
        pool = ConnectionPool()
    except Exception as exc:
        logger.error("Cannot open connection pool: %s", exc)
        return 1

    try:
        applied = Migrator(pool).run()
        if applied:
            print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
        else:
            print("Database is up to date.")
        return 0
    except MigrationError as exc:
        logger.error("%s", exc)
        return 1
    finally:
        pool.close()


if __name__ == "__main__":
    sys.exit(main())

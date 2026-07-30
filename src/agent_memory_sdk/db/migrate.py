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

Schema policy (ORC-4)
---------------------
``Migrator`` now accepts a ``schema_policy`` constructor argument:

``SchemaPolicy.CREATE_IF_NECESSARY`` (default)
    Today's behaviour — apply pending migrations, creating tables and
    indexes as needed.

``SchemaPolicy.REQUIRE_EXISTING``
    Validate that **every** expected table, column, and vector index
    already exists by querying the Db2 SYSCAT catalog.  No DDL is ever
    executed.  If anything is missing, raises :class:`SchemaPolicyError`
    with a single, complete, actionable message listing all missing
    objects so the DBA can provision them in one pass.

    Intended for enterprise deployments where the application user does
    not have DDL privileges and a separate DBA change-management process
    provisions the schema before the application is started.

Usage (from repo root)::

    # Apply all pending migrations (default policy):
    python -m agent_memory_sdk.db.migrate

    # Or from a script:
    from agent_memory_sdk.db.connection import ConnectionPool
    from agent_memory_sdk.db.migrate import Migrator
    pool = ConnectionPool()
    Migrator(pool).run()

    # With REQUIRE_EXISTING policy:
    from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy
    Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING).validate()
"""

from __future__ import annotations

import enum
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

# ---------------------------------------------------------------------------
# Expected schema objects — authoritative list derived from all migrations.
# Migrator.validate() compares this manifest against the SYSCAT catalog.
# ---------------------------------------------------------------------------

# Tables the application requires (TABNAME values as stored in SYSCAT.TABLES).
# The tracking table itself is excluded — it is only required by the runner,
# not by the application layer.
_REQUIRED_TABLES: tuple[str, ...] = (
    "SCHEMA_MIGRATIONS",
    "WORKING_MEMORY",
    "EPISODIC_MEMORY",
    "SEMANTIC_FACTS",
    "ENTITY_PROFILES",
    "PROCEDURAL_MEMORY",
    "MEMORY_CHUNKS",
)

# Required columns per table: {TABNAME: {COLNAME, ...}}.
# Includes every column added across all migrations (0002–0006).
# SYSCAT stores names in UPPER CASE.
_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "SCHEMA_MIGRATIONS": {"VERSION", "APPLIED_AT"},
    "WORKING_MEMORY": {
        "ID", "TENANT_ID", "AGENT_ID", "USER_ID", "THREAD_ID",
        "CONTENT", "METADATA", "EMBEDDING",
        "CREATED_AT", "UPDATED_AT", "EXPIRES_AT", "VERSION", "DELETED_AT",
        # 0003
        "CONFIDENCE", "CONTENT_HASH",
        # 0005
        "CONSOLIDATED_AT",
    },
    "EPISODIC_MEMORY": {
        "ID", "TENANT_ID", "AGENT_ID", "USER_ID", "THREAD_ID",
        "CONTENT", "METADATA", "EMBEDDING",
        "CREATED_AT", "UPDATED_AT", "EXPIRES_AT", "VERSION", "DELETED_AT",
        # 0003
        "CONFIDENCE", "CONTENT_HASH",
        # 0005
        "CONSOLIDATED_AT",
    },
    "SEMANTIC_FACTS": {
        "ID", "TENANT_ID", "AGENT_ID", "USER_ID", "THREAD_ID",
        "CONTENT", "METADATA", "EMBEDDING",
        "CREATED_AT", "UPDATED_AT", "EXPIRES_AT", "VERSION", "DELETED_AT",
        # 0003
        "CONFIDENCE", "CONTENT_HASH",
        # 0004
        "SUPERSEDED_BY", "SUPERSEDED_AT", "SUPERSEDE_REASON",
    },
    "ENTITY_PROFILES": {
        "ID", "TENANT_ID", "AGENT_ID", "USER_ID", "THREAD_ID",
        "CONTENT", "METADATA", "EMBEDDING",
        "CREATED_AT", "UPDATED_AT", "EXPIRES_AT", "VERSION", "DELETED_AT",
        # 0003
        "CONFIDENCE", "CONTENT_HASH",
    },
    "PROCEDURAL_MEMORY": {
        "ID", "TENANT_ID", "AGENT_ID", "USER_ID", "THREAD_ID",
        "CONTENT", "METADATA", "EMBEDDING",
        "CREATED_AT", "UPDATED_AT", "EXPIRES_AT", "VERSION", "DELETED_AT",
        # 0003
        "CONFIDENCE", "CONTENT_HASH",
    },
    "MEMORY_CHUNKS": {
        "ID", "SOURCE_TABLE", "SOURCE_ID", "CHUNK_INDEX", "CHUNK_TEXT",
        "EMBEDDING", "TENANT_ID", "AGENT_ID", "USER_ID", "THREAD_ID",
        "CREATED_AT",
    },
}

# Required indexes per table: {TABNAME: {INDNAME, ...}}.
# Only application indexes are listed — Db2 will create the primary-key index
# automatically, so we do not list those.
_REQUIRED_INDEXES: dict[str, set[str]] = {
    "WORKING_MEMORY": {
        "IX_WORKING_MEMORY_EMBEDDING",
        "IX_WORKING_MEMORY_SCOPE",
        "IX_WORKING_MEMORY_AGENT",
        "IX_WORKING_MEMORY_EXPIRES",
        "IX_WORKING_MEMORY_CONTENT_HASH",
        "IX_WORKING_MEMORY_CONSOLIDATED_AT",
    },
    "EPISODIC_MEMORY": {
        "IX_EPISODIC_MEMORY_EMBEDDING",
        "IX_EPISODIC_MEMORY_SCOPE",
        "IX_EPISODIC_MEMORY_AGENT",
        "IX_EPISODIC_MEMORY_EXPIRES",
        "IX_EPISODIC_MEMORY_CONTENT_HASH",
        "IX_EPISODIC_MEMORY_CONSOLIDATED_AT",
    },
    "SEMANTIC_FACTS": {
        "IX_SEMANTIC_FACTS_EMBEDDING",
        "IX_SEMANTIC_FACTS_SCOPE",
        "IX_SEMANTIC_FACTS_AGENT",
        "IX_SEMANTIC_FACTS_EXPIRES",
        "IX_SEMANTIC_FACTS_CONTENT_HASH",
        "IX_SEMANTIC_FACTS_SUPERSEDED_BY",
    },
    "ENTITY_PROFILES": {
        "IX_ENTITY_PROFILES_EMBEDDING",
        "IX_ENTITY_PROFILES_SCOPE",
        "IX_ENTITY_PROFILES_AGENT",
        "IX_ENTITY_PROFILES_EXPIRES",
        "IX_ENTITY_PROFILES_CONTENT_HASH",
    },
    "PROCEDURAL_MEMORY": {
        "IX_PROCEDURAL_MEMORY_EMBEDDING",
        "IX_PROCEDURAL_MEMORY_SCOPE",
        "IX_PROCEDURAL_MEMORY_AGENT",
        "IX_PROCEDURAL_MEMORY_EXPIRES",
        "IX_PROCEDURAL_MEMORY_CONTENT_HASH",
    },
    "MEMORY_CHUNKS": {
        "IX_MEMORY_CHUNKS_EMBEDDING",
        "IX_MEMORY_CHUNKS_PARENT",
        "IX_MEMORY_CHUNKS_SCOPE",
    },
}


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


# ---------------------------------------------------------------------------
# SchemaPolicy
# ---------------------------------------------------------------------------

class SchemaPolicy(enum.Enum):
    """Controls how :class:`Migrator` handles schema initialisation.

    Attributes:
        CREATE_IF_NECESSARY: Run pending migrations, creating tables and
            indexes as needed.  This is the default and preserves all
            existing behaviour.
        REQUIRE_EXISTING: Validate that every expected table, column, and
            vector index already exists by querying the Db2 ``SYSCAT``
            catalog.  Never execute any DDL.  Raises
            :class:`~agent_memory_sdk.exceptions.SchemaPolicyError` if
            anything is missing, with a single message listing all missing
            objects so the DBA can provision them in one pass.

    Example — enterprise deployment where the DBA provisions the schema::

        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy
        from agent_memory_sdk.db.connection import ConnectionPool

        pool = ConnectionPool()
        # Raises SchemaPolicyError immediately on startup if schema is incomplete.
        Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING).validate()
    """

    CREATE_IF_NECESSARY = "create_if_necessary"
    REQUIRE_EXISTING = "require_existing"


class Migrator:
    """Apply pending .sql migrations against a ConnectionPool.

    Args:
        pool: An open :class:`~agent_memory_sdk.db.connection.ConnectionPool`.
        migrations_dir: Override the default migrations directory (for testing).
        schema_policy: A :class:`SchemaPolicy` value controlling DDL behaviour.
            Defaults to :attr:`SchemaPolicy.CREATE_IF_NECESSARY` (existing
            behaviour — run pending migrations).  Pass
            :attr:`SchemaPolicy.REQUIRE_EXISTING` to switch to validation-only
            mode: :meth:`run` will call :meth:`validate` instead of applying
            migrations, raising :class:`~agent_memory_sdk.exceptions.SchemaPolicyError`
            if the schema is incomplete.
    """

    def __init__(
        self,
        pool: ConnectionPool,
        migrations_dir: Path | None = None,
        schema_policy: SchemaPolicy = SchemaPolicy.CREATE_IF_NECESSARY,
    ) -> None:
        self._pool = pool
        self._dir = migrations_dir or MIGRATIONS_DIR
        self._policy = schema_policy

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> list[str]:
        """Apply all pending migrations in order, or validate the schema.

        When :attr:`SchemaPolicy.CREATE_IF_NECESSARY` is active (default):
            Applies all pending ``.sql`` migration files in lexicographic
            order and returns the list of version strings applied.

        When :attr:`SchemaPolicy.REQUIRE_EXISTING` is active:
            Calls :meth:`validate` and returns an empty list if the schema
            is complete.  Raises
            :class:`~agent_memory_sdk.exceptions.SchemaPolicyError` if
            anything is missing.

        Returns:
            List of version strings that were applied in this run
            (always empty when ``REQUIRE_EXISTING`` is active).
        """
        if self._policy is SchemaPolicy.REQUIRE_EXISTING:
            self.validate()
            return []

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

    def validate(self) -> None:
        """Validate that every expected table, column, and vector index exists.

        Queries ``SYSCAT.TABLES``, ``SYSCAT.COLUMNS``, and ``SYSCAT.INDEXES``
        in three read-only catalog round-trips.  Does **not** execute any DDL.

        Raises:
            :class:`~agent_memory_sdk.exceptions.SchemaPolicyError`:
                If any table, column, or index is missing.  The message
                lists all missing objects so the DBA can provision them in
                one pass.

        This method is called automatically by :meth:`run` when
        :attr:`SchemaPolicy.REQUIRE_EXISTING` is active.  It can also be
        called directly from application startup code regardless of policy.
        """
        from agent_memory_sdk.exceptions import SchemaPolicyError

        missing: list[str] = []

        with self._pool.get_connection() as conn:
            cur = conn.cursor()

            # ----------------------------------------------------------
            # 1. Check tables via SYSCAT.TABLES
            #    Db2 stores TABNAME in UPPER CASE; filter on TABSCHEMA to
            #    restrict to the current user's schema (avoids collisions
            #    with system tables that share names).
            # ----------------------------------------------------------
            cur.execute(
                "SELECT UPPER(TABNAME) FROM SYSCAT.TABLES"
                " WHERE TABSCHEMA = UPPER(CURRENT SCHEMA)"
                "   AND TYPE = 'T'"
            )
            existing_tables: set[str] = {row[0] for row in cur.fetchall()}

            for table in _REQUIRED_TABLES:
                if table not in existing_tables:
                    missing.append(f"table: {table}")

            # ----------------------------------------------------------
            # 2. Check columns via SYSCAT.COLUMNS
            #    Only check columns in tables that DO exist — this keeps
            #    the error output clean when whole tables are absent.
            # ----------------------------------------------------------
            present_tables = existing_tables & set(_REQUIRED_TABLES)
            if present_tables:
                placeholders = ", ".join("?" * len(present_tables))
                cur.execute(
                    f"SELECT UPPER(TABNAME), UPPER(COLNAME)"
                    f"  FROM SYSCAT.COLUMNS"
                    f" WHERE TABSCHEMA = UPPER(CURRENT SCHEMA)"
                    f"   AND UPPER(TABNAME) IN ({placeholders})",  # nosec B608 — placeholders is a literal "?,?,…" string (len(present_tables) question marks); the actual table names from _REQUIRED_TABLES are passed as bound params, not interpolated. DECISIONS.md VER-5.
                    list(present_tables),
                )
                existing_cols: dict[str, set[str]] = {}
                for tab, col in cur.fetchall():
                    existing_cols.setdefault(tab, set()).add(col)

                for table, expected_cols in _REQUIRED_COLUMNS.items():
                    if table not in present_tables:
                        continue  # already reported as missing table
                    actual = existing_cols.get(table, set())
                    for col in sorted(expected_cols - actual):
                        missing.append(f"column: {table}.{col}")

            # ----------------------------------------------------------
            # 3. Check indexes via SYSCAT.INDEXES
            #    Only check indexes on tables that exist.
            # ----------------------------------------------------------
            if present_tables:
                placeholders = ", ".join("?" * len(present_tables))
                cur.execute(
                    f"SELECT UPPER(TABNAME), UPPER(INDNAME)"
                    f"  FROM SYSCAT.INDEXES"
                    f" WHERE TABSCHEMA = UPPER(CURRENT SCHEMA)"
                    f"   AND UPPER(TABNAME) IN ({placeholders})",  # nosec B608 — placeholders is a literal "?,?,…" string; table names from _REQUIRED_TABLES (hardcoded constant) are passed as bound params. DECISIONS.md VER-5.
                    list(present_tables),
                )
                existing_idxs: dict[str, set[str]] = {}
                for tab, idx in cur.fetchall():
                    existing_idxs.setdefault(tab, set()).add(idx)

                for table, expected_idxs in _REQUIRED_INDEXES.items():
                    if table not in present_tables:
                        continue  # already reported as missing table
                    actual = existing_idxs.get(table, set())
                    for idx in sorted(expected_idxs - actual):
                        missing.append(f"index: {idx} on {table}")

        if missing:
            lines = "\n  ".join(missing)
            raise SchemaPolicyError(
                f"REQUIRE_EXISTING validation failed: {len(missing)} object(s) are missing "
                f"from the database schema. Create them before starting the application:\n"
                f"\n  {lines}\n"
                f"\nRun the standard migration runner (SchemaPolicy.CREATE_IF_NECESSARY) "
                f"or apply the DDL manually using the .sql files in "
                f"src/agent_memory_sdk/db/migrations/."
            )

        logger.info("REQUIRE_EXISTING validation passed: all schema objects are present.")

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
            except Exception:  # nosec B110 — intentional probe: we SELECT to test existence; any exception (table missing, driver error) means the table is absent and we create it. The pass is deliberate. DECISIONS.md VER-5.
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

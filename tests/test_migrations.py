"""
tests/test_migrations.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the migration runner (db/migrate.py).
Does NOT require a live Db2 instance — uses a real SQLite in-memory database
as the backing store, since the migration runner only uses DB-API 2.0 methods
(execute, commit, fetchall) which SQLite fully supports.

This approach is deliberately different from mocking: by running against a
real DB-API cursor we validate the SQL statement splitting, ordering logic,
version-tracking, and error propagation without needing ibm_db.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent

import pytest

# ---------------------------------------------------------------------------
# SQLite-backed fake ConnectionPool
# ---------------------------------------------------------------------------

class _SqliteConnection:
    """Thin wrapper matching the ibm_db_dbi.Connection interface used by Migrator."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


class _SqlitePool:
    """Fake ConnectionPool backed by a single in-memory SQLite connection."""

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)

    @contextmanager
    def get_connection(self):
        yield _SqliteConnection(self._conn)

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _migrator_with_sql(tmp_path: Path, *sql_files: tuple[str, str]):
    """Create a Migrator pointing at tmp_path with given (filename, content) pairs."""
    from agent_memory_sdk.db.migrate import Migrator

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    for name, content in sql_files:
        (migrations_dir / name).write_text(content, encoding="utf-8")

    pool = _SqlitePool()
    # Bootstrap schema_migrations using SQLite-compatible CREATE TABLE
    pool._conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version TEXT NOT NULL PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    pool._conn.commit()

    return Migrator(pool, migrations_dir=migrations_dir), pool


# ---------------------------------------------------------------------------
# Tests: _split_statements
# ---------------------------------------------------------------------------

class TestSplitStatements:
    def test_single_statement(self):
        from agent_memory_sdk.db.migrate import _split_statements
        stmts = _split_statements("SELECT 1 FROM DUAL")
        assert stmts == ["SELECT 1 FROM DUAL"]

    def test_multiple_statements(self):
        from agent_memory_sdk.db.migrate import _split_statements
        sql = "CREATE TABLE a (id INT);\nCREATE TABLE b (id INT);"
        stmts = _split_statements(sql)
        assert len(stmts) == 2
        assert "CREATE TABLE a" in stmts[0]
        assert "CREATE TABLE b" in stmts[1]

    def test_comments_stripped(self):
        from agent_memory_sdk.db.migrate import _split_statements
        sql = "-- This is a comment\nCREATE TABLE c (id INT);"
        stmts = _split_statements(sql)
        assert len(stmts) == 1
        assert "comment" not in stmts[0]

    def test_blank_after_semicolon_ignored(self):
        from agent_memory_sdk.db.migrate import _split_statements
        sql = "SELECT 1;   \n   ;"
        stmts = _split_statements(sql)
        assert len(stmts) == 1


# ---------------------------------------------------------------------------
# Tests: Migrator.run
# ---------------------------------------------------------------------------

class TestMigratorRun:
    def test_applies_single_migration(self, tmp_path):
        m, pool = _migrator_with_sql(
            tmp_path,
            ("0001_create_foo.sql", "CREATE TABLE foo (id INTEGER PRIMARY KEY)"),
        )
        applied = m.run()
        assert applied == ["0001_create_foo"]
        # Table should exist
        cur = pool._conn.cursor()
        cur.execute("SELECT COUNT(*) FROM foo")
        assert cur.fetchone()[0] == 0
        pool.close()

    def test_applies_in_order(self, tmp_path):
        m, pool = _migrator_with_sql(
            tmp_path,
            ("0001_a.sql", "CREATE TABLE ta (x INTEGER)"),
            ("0002_b.sql", "CREATE TABLE tb (x INTEGER)"),
        )
        applied = m.run()
        assert applied == ["0001_a", "0002_b"]
        pool.close()

    def test_skips_already_applied(self, tmp_path):
        m, pool = _migrator_with_sql(
            tmp_path,
            ("0001_once.sql", "CREATE TABLE once (x INTEGER)"),
        )
        first  = m.run()
        second = m.run()
        assert first  == ["0001_once"]
        assert second == []
        pool.close()

    def test_empty_directory_returns_empty(self, tmp_path):
        from agent_memory_sdk.db.migrate import Migrator
        migrations_dir = tmp_path / "empty"
        migrations_dir.mkdir()
        p = _SqlitePool()
        p._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version TEXT NOT NULL PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        p._conn.commit()
        m = Migrator(p, migrations_dir=migrations_dir)
        assert m.run() == []
        p.close()

    def test_failed_statement_raises_migration_error(self, tmp_path):
        from agent_memory_sdk.db.migrate import MigrationError
        m, pool = _migrator_with_sql(
            tmp_path,
            ("0001_bad.sql", "THIS IS NOT VALID SQL"),
        )
        with pytest.raises(MigrationError, match="0001_bad"):
            m.run()
        pool.close()

    def test_failed_migration_not_recorded(self, tmp_path):
        """A migration that fails mid-way should NOT appear in schema_migrations."""
        from agent_memory_sdk.db.migrate import MigrationError
        m, pool = _migrator_with_sql(
            tmp_path,
            ("0001_partial.sql", "CREATE TABLE ok (x INTEGER);\nINVALID SQL HERE"),
        )
        with pytest.raises(MigrationError):
            m.run()
        cur = pool._conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = '0001_partial'"
        )
        assert cur.fetchone()[0] == 0
        pool.close()

    def test_multi_statement_migration(self, tmp_path):
        m, pool = _migrator_with_sql(
            tmp_path,
            (
                "0001_multi.sql",
                dedent("""\
                    -- create two tables
                    CREATE TABLE alpha (id INTEGER PRIMARY KEY);
                    CREATE TABLE beta  (id INTEGER PRIMARY KEY);
                """),
            ),
        )
        m.run()
        for tbl in ("alpha", "beta"):
            cur = pool._conn.execute(f"SELECT COUNT(*) FROM {tbl}")
            assert cur.fetchone()[0] == 0
        pool.close()


# ---------------------------------------------------------------------------
# Tests: Migrator.status
# ---------------------------------------------------------------------------

class TestMigratorStatus:
    def test_all_pending_before_run(self, tmp_path):
        m, pool = _migrator_with_sql(
            tmp_path,
            ("0001_a.sql", "CREATE TABLE a (x INTEGER)"),
            ("0002_b.sql", "CREATE TABLE b (x INTEGER)"),
        )
        status = m.status()
        assert status == {"0001_a": "pending", "0002_b": "pending"}
        pool.close()

    def test_applied_after_run(self, tmp_path):
        m, pool = _migrator_with_sql(
            tmp_path,
            ("0001_a.sql", "CREATE TABLE a (x INTEGER)"),
            ("0002_b.sql", "CREATE TABLE b (x INTEGER)"),
        )
        m.run()
        status = m.status()
        assert status == {"0001_a": "applied", "0002_b": "applied"}
        pool.close()

    def test_mixed_status(self, tmp_path):
        m, pool = _migrator_with_sql(
            tmp_path,
            ("0001_a.sql", "CREATE TABLE a (x INTEGER)"),
        )
        m.run()
        # Simulate a new migration file appearing after first run
        (tmp_path / "migrations" / "0002_new.sql").write_text(
            "CREATE TABLE new_tbl (x INTEGER)", encoding="utf-8"
        )
        status = m.status()
        assert status["0001_a"] == "applied"
        assert status["0002_new"] == "pending"
        pool.close()


# ---------------------------------------------------------------------------
# Tests: SchemaPolicy.REQUIRE_EXISTING
# ---------------------------------------------------------------------------
#
# validate() issues three SYSCAT catalog queries.  On a real Db2 instance those
# views exist; in the test suite we use SQLite and stub the three views with
# real in-memory tables that we populate to simulate "everything present" vs.
# "something missing".
#
# The approach: build a _SyscatPool that pre-creates synthetic
# SYSCAT_TABLES / SYSCAT_COLUMNS / SYSCAT_INDEXES SQLite tables, then
# monkey-patch validate() to redirect the three catalog queries to those
# tables.  We patch at the function level — the SQL strings are simple enough
# that we can intercept via a wrapper cursor that rewrites the queries.

class _RewritingCursor:
    """Wraps a sqlite3 cursor and rewrites SYSCAT queries to SQLite equivalents."""

    def __init__(self, cur: sqlite3.Cursor) -> None:
        self._cur = cur
        self._last_rows: list[tuple] = []

    # ------- query rewriting -------

    @staticmethod
    def _rewrite(sql: str, params: list) -> tuple[str, list]:
        """Map Db2 SYSCAT catalog queries to in-memory SQLite table queries."""
        s = sql.upper()
        if "SYSCAT.TABLES" in s:
            # SELECT UPPER(TABNAME) FROM SYSCAT.TABLES
            # WHERE TABSCHEMA = UPPER(CURRENT SCHEMA) AND TYPE = 'T'
            return (
                "SELECT TABNAME FROM _syscat_tables WHERE TYPE = 'T'",
                [],
            )
        if "SYSCAT.COLUMNS" in s:
            # SELECT UPPER(TABNAME), UPPER(COLNAME) FROM SYSCAT.COLUMNS
            # WHERE TABSCHEMA = ... AND UPPER(TABNAME) IN (?, ...)
            n = len(params)
            ph = ", ".join("?" * n)
            return (
                f"SELECT TABNAME, COLNAME FROM _syscat_columns"
                f" WHERE TABNAME IN ({ph})",
                params,
            )
        if "SYSCAT.INDEXES" in s:
            n = len(params)
            ph = ", ".join("?" * n)
            return (
                f"SELECT TABNAME, INDNAME FROM _syscat_indexes"
                f" WHERE TABNAME IN ({ph})",
                params,
            )
        return sql, params

    def execute(self, sql: str, params=None):
        rewritten_sql, rewritten_params = self._rewrite(sql, list(params or []))
        if rewritten_params:
            self._cur.execute(rewritten_sql, rewritten_params)
        else:
            self._cur.execute(rewritten_sql)
        return self

    def fetchall(self):
        return self._cur.fetchall()

    def fetchone(self):
        return self._cur.fetchone()


class _RewritingConnection:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def cursor(self):
        return _RewritingCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


class _SyscatPool:
    """SQLite pool pre-populated with synthetic SYSCAT tables."""

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._setup()

    def _setup(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS _syscat_tables (
                TABNAME TEXT NOT NULL PRIMARY KEY,
                TYPE    TEXT NOT NULL DEFAULT 'T'
            );
            CREATE TABLE IF NOT EXISTS _syscat_columns (
                TABNAME TEXT NOT NULL,
                COLNAME TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS _syscat_indexes (
                TABNAME TEXT NOT NULL,
                INDNAME TEXT NOT NULL
            );
        """)
        self._conn.commit()

    @contextmanager
    def get_connection(self):
        yield _RewritingConnection(self._conn)

    def close(self):
        self._conn.close()

    # helpers to populate the fake catalog ----------

    def add_table(self, name: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO _syscat_tables (TABNAME, TYPE) VALUES (?, 'T')",
            (name.upper(),),
        )
        self._conn.commit()

    def add_column(self, table: str, col: str) -> None:
        self._conn.execute(
            "INSERT INTO _syscat_columns (TABNAME, COLNAME) VALUES (?, ?)",
            (table.upper(), col.upper()),
        )
        self._conn.commit()

    def add_index(self, table: str, idx: str) -> None:
        self._conn.execute(
            "INSERT INTO _syscat_indexes (TABNAME, INDNAME) VALUES (?, ?)",
            (table.upper(), idx.upper()),
        )
        self._conn.commit()


def _full_pool() -> _SyscatPool:
    """Return a _SyscatPool that contains ALL required tables, columns, and indexes."""
    from agent_memory_sdk.db.migrate import (
        _REQUIRED_COLUMNS,
        _REQUIRED_INDEXES,
        _REQUIRED_TABLES,
    )

    pool = _SyscatPool()
    for tbl in _REQUIRED_TABLES:
        pool.add_table(tbl)
    for tbl, cols in _REQUIRED_COLUMNS.items():
        for col in cols:
            pool.add_column(tbl, col)
    for tbl, idxs in _REQUIRED_INDEXES.items():
        for idx in idxs:
            pool.add_index(tbl, idx)
    return pool


class TestSchemaPolicy:
    def test_create_if_necessary_is_default(self, tmp_path):
        """Default policy should remain CREATE_IF_NECESSARY."""
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy

        migrations_dir = tmp_path / "m"
        migrations_dir.mkdir()
        pool = _SqlitePool()
        pool._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version TEXT NOT NULL PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        pool._conn.commit()
        m = Migrator(pool, migrations_dir=migrations_dir)
        assert m._policy is SchemaPolicy.CREATE_IF_NECESSARY
        pool.close()

    def test_validate_passes_when_schema_complete(self):
        """validate() must NOT raise when every required object is present."""
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy

        pool = _full_pool()
        m = Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING)
        m.validate()  # must not raise
        pool.close()

    def test_validate_raises_on_missing_table(self):
        """Validate should raise SchemaPolicyError listing the missing table."""
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy
        from agent_memory_sdk.exceptions import SchemaPolicyError

        pool = _full_pool()
        # Remove MEMORY_CHUNKS from fake catalog
        pool._conn.execute("DELETE FROM _syscat_tables WHERE TABNAME = 'MEMORY_CHUNKS'")
        pool._conn.commit()

        m = Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING)
        with pytest.raises(SchemaPolicyError) as exc_info:
            m.validate()

        msg = str(exc_info.value)
        assert "table: MEMORY_CHUNKS" in msg
        pool.close()

    def test_validate_raises_on_missing_column(self):
        """validate() should list a missing column in its error."""
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy
        from agent_memory_sdk.exceptions import SchemaPolicyError

        pool = _full_pool()
        pool._conn.execute(
            "DELETE FROM _syscat_columns WHERE TABNAME='WORKING_MEMORY' AND COLNAME='CONSOLIDATED_AT'"
        )
        pool._conn.commit()

        m = Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING)
        with pytest.raises(SchemaPolicyError) as exc_info:
            m.validate()

        msg = str(exc_info.value)
        assert "column: WORKING_MEMORY.CONSOLIDATED_AT" in msg
        pool.close()

    def test_validate_raises_on_missing_index(self):
        """validate() should list a missing index in its error."""
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy
        from agent_memory_sdk.exceptions import SchemaPolicyError

        pool = _full_pool()
        pool._conn.execute(
            "DELETE FROM _syscat_indexes"
            " WHERE TABNAME='SEMANTIC_FACTS' AND INDNAME='IX_SEMANTIC_FACTS_EMBEDDING'"
        )
        pool._conn.commit()

        m = Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING)
        with pytest.raises(SchemaPolicyError) as exc_info:
            m.validate()

        msg = str(exc_info.value)
        assert "index: IX_SEMANTIC_FACTS_EMBEDDING on SEMANTIC_FACTS" in msg
        pool.close()

    def test_validate_aggregates_multiple_missing_objects(self):
        """A single SchemaPolicyError should list ALL missing objects at once."""
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy
        from agent_memory_sdk.exceptions import SchemaPolicyError

        pool = _full_pool()
        # Remove a table, a column from another table, and an index
        pool._conn.execute("DELETE FROM _syscat_tables WHERE TABNAME='PROCEDURAL_MEMORY'")
        pool._conn.execute(
            "DELETE FROM _syscat_columns WHERE TABNAME='EPISODIC_MEMORY' AND COLNAME='CONFIDENCE'"
        )
        pool._conn.execute(
            "DELETE FROM _syscat_indexes WHERE TABNAME='ENTITY_PROFILES' AND INDNAME='IX_ENTITY_PROFILES_SCOPE'"
        )
        pool._conn.commit()

        m = Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING)
        with pytest.raises(SchemaPolicyError) as exc_info:
            m.validate()

        msg = str(exc_info.value)
        assert "table: PROCEDURAL_MEMORY" in msg
        assert "column: EPISODIC_MEMORY.CONFIDENCE" in msg
        assert "index: IX_ENTITY_PROFILES_SCOPE on ENTITY_PROFILES" in msg
        pool.close()

    def test_run_with_require_existing_returns_empty_list_on_success(self):
        """run() with REQUIRE_EXISTING should return [] when schema is complete."""
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy

        pool = _full_pool()
        m = Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING)
        result = m.run()
        assert result == []
        pool.close()

    def test_run_with_require_existing_raises_on_incomplete_schema(self):
        """run() with REQUIRE_EXISTING must propagate SchemaPolicyError."""
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy
        from agent_memory_sdk.exceptions import SchemaPolicyError

        pool = _SyscatPool()  # empty — no tables present
        m = Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING)
        with pytest.raises(SchemaPolicyError) as exc_info:
            m.run()

        msg = str(exc_info.value)
        # Every required table must appear in the error message
        for tbl in ("WORKING_MEMORY", "EPISODIC_MEMORY", "SEMANTIC_FACTS",
                    "ENTITY_PROFILES", "PROCEDURAL_MEMORY", "MEMORY_CHUNKS"):
            assert f"table: {tbl}" in msg
        pool.close()

    def test_require_existing_does_not_run_ddl(self):
        """REQUIRE_EXISTING must never bootstrap or create tables."""
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy
        from agent_memory_sdk.exceptions import SchemaPolicyError

        # Use a pool with no tables at all (not even the SQLite helpers)
        pool = _SyscatPool()  # empty catalog
        m = Migrator(pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING)

        with pytest.raises(SchemaPolicyError):
            m.run()

        # Confirm that _bootstrap (DDL) was never called — schema_migrations
        # must NOT exist in the underlying SQLite db.
        cur = pool._conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        )
        assert cur.fetchone() is None, "REQUIRE_EXISTING must never run DDL"
        pool.close()

    def test_schema_policy_exported_from_top_level(self):
        """SchemaPolicy and SchemaPolicyError must be importable from the package root."""
        import agent_memory_sdk
        assert hasattr(agent_memory_sdk, "SchemaPolicy")
        assert hasattr(agent_memory_sdk, "SchemaPolicyError")
        from agent_memory_sdk import SchemaPolicy, SchemaPolicyError  # noqa: F401


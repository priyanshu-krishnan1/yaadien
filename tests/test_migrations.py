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

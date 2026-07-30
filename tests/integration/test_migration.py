"""
tests/integration/test_migration.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for the SQL migration runner.

Verifies:
- Migrator.run() applies all pending migrations and returns their names.
- Re-running Migrator.run() is idempotent (returns empty list; no errors).
- schema_migrations table records every applied version.
- Migrator.status() reports all migrations as 'applied' after a run.
- The five memory tables exist and have the expected columns after migration.
- The vector index DDL was accepted (CREATE VECTOR INDEX succeeded).

These tests run against the *shared* migrated_pool session fixture, so the
tables are created once and reused.  Each assertion is read-only (SELECT
only) so there is no teardown needed.

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_TABLES = [
    "working_memory",
    "episodic_memory",
    "semantic_facts",
    "entity_profiles",
    "procedural_memory",
]

_EXPECTED_COLUMNS = [
    "ID",
    "TENANT_ID",
    "AGENT_ID",
    "USER_ID",
    "THREAD_ID",
    "CONTENT",
    "METADATA",
    "EMBEDDING",
    "CREATED_AT",
    "UPDATED_AT",
    "EXPIRES_AT",
    "VERSION",
    "DELETED_AT",
    # Added by migration 0003 (ENH-1/ENH-2)
    "CONFIDENCE",
    "CONTENT_HASH",
]

_EXPECTED_VERSIONS = [
    "0001_schema_migrations",
    "0002_memory_tables",
    "0003_confidence_and_content_hash",
    "0004_supersession",
    "0005_consolidated_at",
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigrationRun:
    """Migrator.run() behaviour against a real Db2 instance."""

    def test_run_is_idempotent(self, migrated_pool):
        """A second run against an already-migrated DB returns empty list."""
        from agent_memory_sdk.db.migrate import Migrator

        result = Migrator(migrated_pool).run()
        assert result == [], (
            f"Expected no new migrations on second run, got: {result}"
        )

    def test_schema_migrations_table_records_all_versions(self, migrated_pool):
        """Every migration file has an entry in schema_migrations."""
        with migrated_pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
            rows = cur.fetchall()

        applied = {row[0] for row in rows}
        for version in _EXPECTED_VERSIONS:
            assert version in applied, (
                f"Migration '{version}' not recorded in schema_migrations"
            )

    def test_status_reports_all_applied(self, migrated_pool):
        """Migrator.status() returns 'applied' for every migration file."""
        from agent_memory_sdk.db.migrate import Migrator

        status = Migrator(migrated_pool).status()
        assert len(status) >= len(_EXPECTED_VERSIONS), (
            f"Expected at least {len(_EXPECTED_VERSIONS)} migrations in status, got {len(status)}"
        )
        for version in _EXPECTED_VERSIONS:
            assert version in status, f"version '{version}' missing from status()"
            assert status[version] == "applied", (
                f"Expected '{version}' to be 'applied', got '{status[version]}'"
            )


class TestTablesExist:
    """Verify the five memory tables were created by 0002_memory_tables.sql."""

    @pytest.mark.parametrize("table_name", _EXPECTED_TABLES)
    def test_table_exists(self, migrated_pool, table_name: str):
        """Each memory table must exist and accept a SELECT COUNT(*)."""
        with migrated_pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            row = cur.fetchone()
        assert row is not None, f"SELECT COUNT(*) FROM {table_name} returned nothing"

    @pytest.mark.parametrize("table_name", _EXPECTED_TABLES)
    def test_table_has_expected_columns(self, migrated_pool, table_name: str):
        """Each table must have all required columns (checked via SYSCAT)."""
        with migrated_pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COLNAME
                FROM SYSCAT.COLUMNS
                WHERE TABNAME = ?
                AND TABSCHEMA = CURRENT SCHEMA
                """,
                (table_name.upper(),),
            )
            rows = cur.fetchall()

        col_names = {row[0].upper() for row in rows}
        for col in _EXPECTED_COLUMNS:
            assert col in col_names, (
                f"Column '{col}' missing from table '{table_name}'. "
                f"Found: {sorted(col_names)}"
            )

    @pytest.mark.parametrize("table_name", _EXPECTED_TABLES)
    def test_embedding_column_is_not_null_vector(self, migrated_pool, table_name: str):
        """The embedding column must be VECTOR type and NOT NULL in SYSCAT."""
        with migrated_pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT NULLS, TYPENAME
                FROM SYSCAT.COLUMNS
                WHERE TABNAME = ?
                AND TABSCHEMA = CURRENT SCHEMA
                AND COLNAME = 'EMBEDDING'
                """,
                (table_name.upper(),),
            )
            row = cur.fetchone()

        assert row is not None, (
            f"EMBEDDING column not found in SYSCAT for '{table_name}'"
        )
        nulls, typename = row
        assert nulls == "N", (
            f"EMBEDDING column in '{table_name}' must be NOT NULL, got NULLS='{nulls}'"
        )
        # Db2 SYSCAT.COLUMNS stores VECTOR type as e.g. "VECTOR"
        assert "VECTOR" in typename.upper(), (
            f"EMBEDDING column in '{table_name}' has unexpected type '{typename}'"
        )

    @pytest.mark.parametrize("table_name", _EXPECTED_TABLES)
    def test_vector_index_exists(self, migrated_pool, table_name: str):
        """A vector index on the embedding column must exist in SYSCAT."""
        expected_index = f"IX_{table_name.upper()}_EMBEDDING"
        with migrated_pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT INDNAME
                FROM SYSCAT.INDEXES
                WHERE TABNAME = ?
                AND TABSCHEMA = CURRENT SCHEMA
                AND INDNAME = ?
                """,
                (table_name.upper(), expected_index),
            )
            row = cur.fetchone()

        assert row is not None, (
            f"Vector index '{expected_index}' not found for table '{table_name}'"
        )

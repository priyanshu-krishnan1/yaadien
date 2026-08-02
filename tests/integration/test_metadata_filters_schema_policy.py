"""
tests/integration/test_metadata_filters_schema_policy.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
LIVE-6 — Integration tests for structured metadata filters (ORC-3) and
SchemaPolicy.REQUIRE_EXISTING (ORC-4).

Metadata filter coverage
------------------------
Seed three SemanticFact rows with distinct metadata shapes and verify that
every supported operator (exact match, $not, $array_contains,
$array_contains_any) returns precisely the expected subset.  Also verifies
the empty-result case and that metadata_filter works on search() as well as
list_all().

SchemaPolicy coverage
---------------------
PASS case: Migrator(migrated_pool, schema_policy=REQUIRE_EXISTING).run()
  on the already-migrated pool must succeed without raising any exception.

FAIL case: Constructing a pool with SET CURRENT SCHEMA pointing at an empty
  (non-existent) schema namespace would require DBA access on enterprise Db2
  to CREATE SCHEMA.  Because ibm_db_dbi infers the default schema from the
  connecting user and we cannot SET CURRENT SCHEMA within the connection
  pool abstraction without ddl privileges, the explicit negative test is
  marked xfail with a clear explanation.  If the Db2 instance is updated to
  allow per-connection schema switching, remove the xfail mark and uncomment
  the body.

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import make_unit_vec

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ids(records) -> set[str]:
    """Return the set of record ids from a list of model instances."""
    return {r.id for r in records}


# ---------------------------------------------------------------------------
# Metadata filter tests (ORC-3)
# ---------------------------------------------------------------------------


class TestMetadataFiltersListAll:
    """list_all() with every supported metadata_filter operator variant."""

    @pytest.fixture()
    def seeded(self, store, scope, vec_dim):
        """Seed fact_A, fact_B, fact_C and return their stored instances.

        fact_A: source=support, priority=high, tags=[python, sdk]
        fact_B: source=docs,    priority=low,  tags=[java, sdk]
        fact_C: source=support, priority=low,  tags=[python]
        """
        from agent_memory_sdk.models import SemanticFact

        fact_a = store.facts.create(
            SemanticFact(
                agent_id=scope.agent_id,
                content="Fact A — python sdk support high priority",
                embedding=make_unit_vec(vec_dim, 0),
                metadata={"source": "support", "priority": "high", "tags": ["python", "sdk"]},
            ),
            scope,
        )
        fact_b = store.facts.create(
            SemanticFact(
                agent_id=scope.agent_id,
                content="Fact B — java sdk docs low priority",
                embedding=make_unit_vec(vec_dim, 1),
                metadata={"source": "docs", "priority": "low", "tags": ["java", "sdk"]},
            ),
            scope,
        )
        fact_c = store.facts.create(
            SemanticFact(
                agent_id=scope.agent_id,
                content="Fact C — python support low priority",
                embedding=make_unit_vec(vec_dim, 2),
                metadata={"source": "support", "priority": "low", "tags": ["python"]},
            ),
            scope,
        )
        return fact_a, fact_b, fact_c

    def test_exact_match_returns_matching_rows(self, store, scope, seeded):
        """Exact-match filter on source='support' must return fact_A and fact_C."""
        fact_a, fact_b, fact_c = seeded

        results = store.facts.list_all(
            scope,
            limit=100,
            metadata_filter={"source": "support"},
        )
        result_ids = _ids(results)

        assert fact_a.id in result_ids, "fact_A (source=support) must be returned"
        assert fact_c.id in result_ids, "fact_C (source=support) must be returned"
        assert fact_b.id not in result_ids, "fact_B (source=docs) must NOT be returned"

    def test_not_operator_excludes_matching_rows(self, store, scope, seeded):
        """$not filter on priority='low' must return only fact_A (priority=high)."""
        fact_a, fact_b, fact_c = seeded

        results = store.facts.list_all(
            scope,
            limit=100,
            metadata_filter={"priority": {"$not": "low"}},
        )
        result_ids = _ids(results)

        assert fact_a.id in result_ids, "fact_A (priority=high) must be returned"
        assert fact_b.id not in result_ids, "fact_B (priority=low) must NOT be returned"
        assert fact_c.id not in result_ids, "fact_C (priority=low) must NOT be returned"

    def test_array_contains_returns_rows_with_value(self, store, scope, seeded):
        """$array_contains 'python' must return fact_A and fact_C (not fact_B)."""
        fact_a, fact_b, fact_c = seeded

        results = store.facts.list_all(
            scope,
            limit=100,
            metadata_filter={"tags": {"$array_contains": "python"}},
        )
        result_ids = _ids(results)

        assert fact_a.id in result_ids, "fact_A (tags includes 'python') must be returned"
        assert fact_c.id in result_ids, "fact_C (tags includes 'python') must be returned"
        assert fact_b.id not in result_ids, "fact_B (tags=['java','sdk']) must NOT be returned"

    def test_array_contains_any_returns_rows_with_any_value(self, store, scope, seeded):
        """$array_contains_any ['java','missing'] must return only fact_B."""
        fact_a, fact_b, fact_c = seeded

        results = store.facts.list_all(
            scope,
            limit=100,
            metadata_filter={"tags": {"$array_contains_any": ["java", "missing"]}},
        )
        result_ids = _ids(results)

        assert fact_b.id in result_ids, "fact_B (tags includes 'java') must be returned"
        assert fact_a.id not in result_ids, "fact_A (tags=['python','sdk']) must NOT be returned"
        assert fact_c.id not in result_ids, "fact_C (tags=['python']) must NOT be returned"

    def test_no_matching_rows_returns_empty_list(self, store, scope, seeded):
        """A filter that matches nothing must return an empty list without error."""
        results = store.facts.list_all(
            scope,
            limit=100,
            metadata_filter={"source": "nonexistent"},
        )
        assert results == [], (
            "Filter with no matching rows must return an empty list, not raise"
        )

    def test_invalid_operator_raises_error(self, store, scope, seeded):
        """An unrecognized $-prefixed operator must raise InvalidMetadataFilterError."""
        from agent_memory_sdk import InvalidMetadataFilterError

        with pytest.raises(InvalidMetadataFilterError):
            store.facts.list_all(
                scope,
                metadata_filter={"key": {"$unknown_op": "value"}},
            )


# ---------------------------------------------------------------------------
# Metadata filter via search() (ORC-3)
# ---------------------------------------------------------------------------


class TestMetadataFiltersSearch:
    """search() must respect metadata_filter just like list_all()."""

    @pytest.fixture()
    def seeded(self, store, scope, vec_dim):
        """Seed fact_A and fact_B with distinct source values and real vectors."""
        from agent_memory_sdk.models import SemanticFact

        # Use orthogonal unit vectors so the query vec is unambiguous.
        fact_a = store.facts.create(
            SemanticFact(
                agent_id=scope.agent_id,
                content="Search fact A — source support",
                embedding=make_unit_vec(vec_dim, 10),
                metadata={"source": "support"},
            ),
            scope,
        )
        fact_b = store.facts.create(
            SemanticFact(
                agent_id=scope.agent_id,
                content="Search fact B — source docs",
                embedding=make_unit_vec(vec_dim, 11),
                metadata={"source": "docs"},
            ),
            scope,
        )
        return fact_a, fact_b

    def test_search_with_metadata_filter_restricts_results(self, store, scope, seeded, vec_dim):
        """search() with metadata_filter={'source':'support'} must exclude source=docs rows."""
        fact_a, fact_b = seeded

        # Query close to fact_A's vector; without a filter both would be candidates.
        results = store.facts.search(
            query_embedding=make_unit_vec(vec_dim, 10),
            scope=scope,
            top_k=50,
            metadata_filter={"source": "support"},
        )
        result_ids = _ids(results)

        assert fact_a.id in result_ids, (
            "fact_A (source=support) must appear in filtered search results"
        )
        assert fact_b.id not in result_ids, (
            "fact_B (source=docs) must NOT appear when filter restricts to source=support"
        )


# ---------------------------------------------------------------------------
# SchemaPolicy.REQUIRE_EXISTING (ORC-4)
# ---------------------------------------------------------------------------


class TestSchemaPolicyRequireExisting:
    """Migrator.run() with REQUIRE_EXISTING on the fully-migrated pool must pass."""

    def test_require_existing_passes_on_migrated_schema(self, migrated_pool):
        """REQUIRE_EXISTING must not raise when all tables/columns/indexes exist."""
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy

        # Must complete without raising any exception.
        Migrator(migrated_pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING).run()

    def test_require_existing_validate_passes_on_migrated_schema(self, migrated_pool):
        """validate() (called directly) must also pass on the migrated pool."""
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy

        Migrator(migrated_pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING).validate()

    @pytest.mark.xfail(
        reason=(
            "Testing the FAIL path of REQUIRE_EXISTING requires constructing a pool "
            "whose connection session points to a schema that has none of the expected "
            "tables.  On IBM Db2 LUW the default schema is the connecting user's name "
            "and cannot be overridden to a truly empty namespace without either (a) "
            "connecting as a different OS user, or (b) issuing SET CURRENT SCHEMA "
            "inside a connection — which is not exposed through the ConnectionPool API "
            "without DBA privileges to CREATE SCHEMA.  Accordingly, the negative test "
            "is documented here but left as xfail.  To activate it on an instance that "
            "permits schema switching: create a fresh schema 'TESTSCHEMA_EMPTY' that "
            "has no application tables, open a pool connecting to that schema, and "
            "assert SchemaPolicyError is raised."
        ),
        strict=False,
    )
    def test_require_existing_fails_on_empty_schema(self, migrated_pool):
        """REQUIRE_EXISTING must raise SchemaPolicyError when tables are absent.

        This test is xfail because constructing a ConnectionPool against a
        provably-empty schema is not feasible in the standard CI environment
        without DBA privileges.  See the mark reason for details.
        """
        from agent_memory_sdk import SchemaPolicyError
        from agent_memory_sdk.db.migrate import Migrator, SchemaPolicy

        # Intentionally unreachable without schema-switch capability — xfail.
        raise pytest.xfail(  # type: ignore[misc]
            "Cannot construct an empty-schema pool in this environment."
        )
        with pytest.raises(SchemaPolicyError):  # pragma: no cover
            Migrator(migrated_pool, schema_policy=SchemaPolicy.REQUIRE_EXISTING).validate()

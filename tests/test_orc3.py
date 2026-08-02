"""
tests/test_orc3.py
~~~~~~~~~~~~~~~~~~
Unit tests for ORC-3: structured metadata filter operators.

Tests the public ``_build_metadata_filter`` function and both call sites
(``list_all()`` and ``search()``).  No live Db2 instance required — uses the
same fake connection pool pattern as the rest of the unit suite.

Coverage:
  - _build_metadata_filter: returns ("", []) when filter is None or {}
  - Exact match → JSON_VALUE(col FORMAT JSON, 'lax $.field') = 'value'
  - $not → JSON_VALUE(col FORMAT JSON, 'lax $.field') <> 'value'
  - $array_contains → triple-LOCATE: field key, '"value"' between field_pos and ']' pos
  - $array_contains_any → OR-joined LOCATE checks for each value
  - Multiple fields in one filter dict
  - Invalid field name → InvalidMetadataFilterError
  - Unrecognized $operator → InvalidMetadataFilterError
  - Non-$ key inside operator dict → InvalidMetadataFilterError
  - $array_contains_any with empty list → InvalidMetadataFilterError
  - Unsupported operand type (list used as plain value) → InvalidMetadataFilterError
  - _escape_json_path_value: strings, numbers, bools, None, SQL-injection chars
  - list_all() SQL contains the metadata predicate; meta_params always empty
  - search() SQL step-1 contains the metadata predicate; meta_params always empty
  - metadata_filter=None is a no-op (no predicate emitted, no params added)
  - Integration: filter combined with min_confidence
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_memory_sdk.exceptions import InvalidMetadataFilterError
from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.repositories.base import (
    _build_metadata_filter,
    _escape_json_path_value,
)
from agent_memory_sdk.repositories.facts import SemanticFactRepository
from agent_memory_sdk.repositories.working import WorkingMemoryRepository

# ---------------------------------------------------------------------------
# Minimal fake pool (mirrors pattern from test_repositories.py)
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount = len(self.rows)

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self.last_sql = sql
        self.last_params = params or []

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        pass


class _FakePool:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor = _FakeCursor(rows)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):  # type: ignore[return]
        yield self.conn


_SCOPE = MemoryScope(agent_id="agent-orc3", user_id="user-1")
_NOW = datetime(2026, 8, 2, 10, 0, 0, tzinfo=timezone.utc)
_VEC = [0.0] * 1536

# ---------------------------------------------------------------------------
# Helper: build a minimal valid SemanticFact row tuple (15 base columns)
# ---------------------------------------------------------------------------

def _fact_row(metadata: str = "{}") -> tuple[Any, ...]:
    return (
        "id-001",         # 0  id
        None,             # 1  tenant_id
        "agent-orc3",     # 2  agent_id
        "user-1",         # 3  user_id
        None,             # 4  thread_id
        "fact content",   # 5  content
        metadata,         # 6  metadata
        "[0.0,0.0]",      # 7  embedding (VECTOR_SERIALIZE)
        1.0,              # 8  confidence
        None,             # 9  content_hash
        _NOW,             # 10 created_at
        _NOW,             # 11 updated_at
        None,             # 12 expires_at
        1,                # 13 version
        None,             # 14 deleted_at
        None,             # 15 superseded_by
        None,             # 16 superseded_at
        None,             # 17 supersede_reason
    )


# ---------------------------------------------------------------------------
# Tests: _build_metadata_filter — SQL generation
# ---------------------------------------------------------------------------

class TestBuildMetadataFilterNoop:
    def test_none_returns_empty(self) -> None:
        sql, params = _build_metadata_filter(None)
        assert sql == ""
        assert params == []

    def test_empty_dict_returns_empty(self) -> None:
        sql, params = _build_metadata_filter({})
        assert sql == ""
        assert params == []


class TestBuildMetadataFilterExactMatch:
    def test_single_string_field(self) -> None:
        sql, params = _build_metadata_filter({"source": "support"})
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.source') = 'support'" in sql
        assert "JSON_VALUE" in sql
        # All values inlined — no bound params for metadata filter
        assert params == []
        assert sql.startswith(" AND ")

    def test_integer_field(self) -> None:
        sql, params = _build_metadata_filter({"priority": 1})
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.priority') = '1'" in sql
        assert params == []

    def test_bool_field(self) -> None:
        sql, params = _build_metadata_filter({"active": True})
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.active') = 'true'" in sql
        assert params == []

    def test_bool_false_field(self) -> None:
        sql, params = _build_metadata_filter({"active": False})
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.active') = 'false'" in sql
        assert params == []

    def test_none_field(self) -> None:
        # A None operand means "field must be absent or explicitly null".
        sql, params = _build_metadata_filter({"key": None})
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.key') IS NULL" in sql
        assert params == []

    def test_multiple_fields(self) -> None:
        sql, params = _build_metadata_filter({"source": "support", "lang": "en"})
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.source') = 'support'" in sql
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.lang') = 'en'" in sql
        # All values inlined — no bound params
        assert params == []
        # Fragment starts with " AND "
        assert sql.startswith(" AND ")


class TestBuildMetadataFilterNot:
    def test_not_string(self) -> None:
        sql, params = _build_metadata_filter({"status": {"$not": "archived"}})
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.status') <> 'archived'" in sql
        assert "JSON_VALUE" in sql
        # All values inlined — no bound params
        assert params == []

    def test_not_integer(self) -> None:
        sql, params = _build_metadata_filter({"priority": {"$not": 5}})
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.priority') <> '5'" in sql
        assert params == []

    def test_not_bool_true(self) -> None:
        sql, params = _build_metadata_filter({"active": {"$not": True}})
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.active') <> 'true'" in sql
        assert params == []

    def test_not_bool_false(self) -> None:
        sql, params = _build_metadata_filter({"active": {"$not": False}})
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.active') <> 'false'" in sql
        assert params == []


class TestBuildMetadataFilterArrayContains:
    def test_array_contains_string(self) -> None:
        sql, params = _build_metadata_filter({"tags": {"$array_contains": "urgent"}})
        # Raw-string triple-LOCATE: field key, value, and closing ] boundary
        assert "LOCATE('\"tags\":', metadata)" in sql
        assert "LOCATE('\"urgent\"', metadata," in sql
        assert "LOCATE(']', metadata," in sql
        # No bound params (value inlined)
        assert params == []

    def test_array_contains_integer(self) -> None:
        sql, params = _build_metadata_filter({"scores": {"$array_contains": 42}})
        assert "LOCATE('\"scores\":', metadata)" in sql
        assert "LOCATE('\"42\"', metadata," in sql
        assert params == []

    def test_array_contains_bool(self) -> None:
        sql, params = _build_metadata_filter({"flags": {"$array_contains": True}})
        assert "LOCATE('\"flags\":', metadata)" in sql
        assert "LOCATE('\"true\"', metadata," in sql
        assert params == []


class TestBuildMetadataFilterArrayContainsAny:
    def test_array_contains_any_two_values(self) -> None:
        sql, params = _build_metadata_filter(
            {"tags": {"$array_contains_any": ["urgent", "bug"]}}
        )
        assert "LOCATE('\"tags\":', metadata)" in sql
        assert "LOCATE('\"urgent\"', metadata," in sql
        assert "LOCATE('\"bug\"', metadata," in sql
        assert params == []

    def test_array_contains_any_single_value(self) -> None:
        sql, params = _build_metadata_filter(
            {"tags": {"$array_contains_any": ["critical"]}}
        )
        assert "LOCATE('\"tags\":', metadata)" in sql
        assert "LOCATE('\"critical\"', metadata," in sql
        assert params == []

    def test_array_contains_any_empty_list_raises(self) -> None:
        with pytest.raises(InvalidMetadataFilterError, match="non-empty list"):
            _build_metadata_filter({"tags": {"$array_contains_any": []}})


class TestBuildMetadataFilterMultipleOperators:
    """A single filter dict may combine different operators for different fields."""

    def test_exact_and_not(self) -> None:
        sql, params = _build_metadata_filter(
            {"source": "support", "status": {"$not": "archived"}}
        )
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.source') = 'support'" in sql
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.status') <> 'archived'" in sql
        # All values inlined — no bound params
        assert params == []

    def test_exact_and_array_contains(self) -> None:
        sql, params = _build_metadata_filter(
            {"source": "kb", "tags": {"$array_contains": "urgent"}}
        )
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.source') = 'kb'" in sql
        assert "LOCATE('\"tags\":', metadata)" in sql
        assert "LOCATE('\"urgent\"', metadata," in sql
        # All values inlined — no bound params
        assert params == []

    def test_bool_exact_and_array_contains_consistent(self) -> None:
        """Exact-match and $array_contains both inline values; no bound params."""
        # exact-match path → value inlined via JSON_VALUE
        exact_sql, exact_params = _build_metadata_filter({"active": True})
        # $array_contains path → value inlined via raw LOCATE
        array_sql, _ = _build_metadata_filter({"flags": {"$array_contains": True}})
        # Both produce no bound params
        assert exact_params == []
        # Exact match uses JSON_VALUE with 'true'
        assert "'true'" in exact_sql
        # Array contains uses raw LOCATE with "true"
        assert '"true"' in array_sql

    def test_bool_false_exact_and_array_contains_consistent(self) -> None:
        """Same consistency check for False."""
        exact_sql, exact_params = _build_metadata_filter({"active": False})
        array_sql, _ = _build_metadata_filter({"flags": {"$array_contains": False}})
        assert exact_params == []
        assert "'false'" in exact_sql
        assert '"false"' in array_sql


# ---------------------------------------------------------------------------
# Tests: _build_metadata_filter — error cases
# ---------------------------------------------------------------------------

class TestBuildMetadataFilterErrors:
    def test_invalid_field_name_with_special_chars(self) -> None:
        with pytest.raises(InvalidMetadataFilterError, match="field name"):
            _build_metadata_filter({"fiel;d": "val"})

    def test_field_name_starts_with_digit(self) -> None:
        with pytest.raises(InvalidMetadataFilterError, match="field name"):
            _build_metadata_filter({"1field": "val"})

    def test_dollar_prefixed_field_name(self) -> None:
        with pytest.raises(InvalidMetadataFilterError, match="field name"):
            _build_metadata_filter({"$field": "val"})

    def test_unrecognized_dollar_operator(self) -> None:
        with pytest.raises(
            InvalidMetadataFilterError,
            match=r"Unrecognized metadata filter operator '\$in'",
        ):
            _build_metadata_filter({"status": {"$in": ["a", "b"]}})

    def test_unrecognized_dollar_operator_error_mentions_supported(self) -> None:
        """The error message must list the supported operators."""
        with pytest.raises(InvalidMetadataFilterError) as exc_info:
            _build_metadata_filter({"x": {"$regex": ".*"}})
        msg = str(exc_info.value)
        assert "$array_contains" in msg
        assert "$array_contains_any" in msg
        assert "$not" in msg

    def test_non_dollar_key_in_operator_dict_raises(self) -> None:
        """A value dict with a non-$ key is rejected (nested objects not supported)."""
        with pytest.raises(InvalidMetadataFilterError, match="Nested object"):
            _build_metadata_filter({"foo": {"bar": "baz"}})

    def test_unsupported_operand_type_list(self) -> None:
        """Passing a plain list as the operand (not wrapped in $array_contains_any) raises."""
        with pytest.raises(InvalidMetadataFilterError, match="Unsupported operand type"):
            _build_metadata_filter({"tags": ["a", "b"]})


# ---------------------------------------------------------------------------
# Tests: _escape_json_path_value
# ---------------------------------------------------------------------------

class TestEscapeJsonPathValue:
    def test_plain_string(self) -> None:
        assert _escape_json_path_value("hello") == '"hello"'

    def test_string_with_single_quote(self) -> None:
        # Single-quote must be doubled for the surrounding SQL literal
        result = _escape_json_path_value("it's")
        assert "'" not in result.replace("''", "")
        assert "''" in result

    def test_string_with_double_quote(self) -> None:
        # Double-quote is the JSON path string boundary — must be backslash-escaped
        result = _escape_json_path_value('say "hi"')
        assert '\\"' in result

    def test_string_with_backslash(self) -> None:
        result = _escape_json_path_value("back\\slash")
        assert "\\\\" in result

    def test_integer(self) -> None:
        assert _escape_json_path_value(42) == "42"

    def test_float(self) -> None:
        assert _escape_json_path_value(3.14) == "3.14"

    def test_true(self) -> None:
        assert _escape_json_path_value(True) == "true"

    def test_false(self) -> None:
        assert _escape_json_path_value(False) == "false"

    def test_none(self) -> None:
        assert _escape_json_path_value(None) == "null"

    def test_sql_injection_attempt_single_quote(self) -> None:
        """A single-quote in the value must not break the SQL literal."""
        val = "a' OR '1'='1"
        result = _escape_json_path_value(val)
        # Single-quote doubled; the string is still safely enclosed in "..."
        assert "'" not in result.replace("''", "")

    def test_sql_injection_attempt_backslash(self) -> None:
        """Backslashes must be escaped before they reach the SQL literal."""
        val = "foo\\'; DROP TABLE memories; --"
        result = _escape_json_path_value(val)
        # Every backslash doubled; single-quote doubled
        assert "\\\\" in result


# ---------------------------------------------------------------------------
# Tests: list_all() SQL and param integration
# ---------------------------------------------------------------------------

class TestListAllWithMetadataFilter:
    def _make_repo(
        self, rows: list[tuple[Any, ...]] | None = None
    ) -> tuple[WorkingMemoryRepository, _FakePool]:
        pool = _FakePool(rows)
        repo = WorkingMemoryRepository(pool)
        return repo, pool

    def test_no_filter_no_predicate(self) -> None:
        repo, pool = self._make_repo()
        repo.list_all(_SCOPE)
        sql = pool.cursor.last_sql
        assert "JSON_VALUE" not in sql
        assert "JSON_EXISTS" not in sql
        assert "LOCATE" not in sql

    def test_exact_match_predicate_in_sql(self) -> None:
        repo, pool = self._make_repo()
        repo.list_all(_SCOPE, metadata_filter={"source": "support"})
        sql = pool.cursor.last_sql
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.source') = 'support'" in sql
        assert "JSON_VALUE" in sql

    def test_exact_match_value_inlined_not_in_params(self) -> None:
        """Metadata filter values are inlined in SQL — no bound params emitted."""
        repo, pool = self._make_repo()
        repo.list_all(_SCOPE, metadata_filter={"source": "support"})
        params = pool.cursor.last_params
        # "support" is inlined in the SQL, not a bound param
        assert "support" not in params
        # Params should be: [agent_id, user_id, limit]
        assert len(params) == 3

    def test_not_predicate_in_sql(self) -> None:
        repo, pool = self._make_repo()
        repo.list_all(_SCOPE, metadata_filter={"status": {"$not": "archived"}})
        sql = pool.cursor.last_sql
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.status') <> 'archived'" in sql
        assert "JSON_VALUE" in sql
        # Value is inlined, not a bound param
        assert "archived" not in pool.cursor.last_params

    def test_array_contains_predicate_in_sql(self) -> None:
        repo, pool = self._make_repo()
        repo.list_all(_SCOPE, metadata_filter={"tags": {"$array_contains": "urgent"}})
        sql = pool.cursor.last_sql
        assert "LOCATE" in sql
        assert "urgent" in sql

    def test_combined_min_confidence_and_metadata_filter(self) -> None:
        """Both predicates must appear in the SQL; only confidence uses bound param."""
        repo, pool = self._make_repo()
        repo.list_all(
            _SCOPE,
            min_confidence=0.7,
            metadata_filter={"source": "kb"},
        )
        sql = pool.cursor.last_sql
        assert "confidence >= ?" in sql
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.source') = 'kb'" in sql
        assert "JSON_VALUE" in sql
        params = pool.cursor.last_params
        assert 0.7 in params
        # "kb" is inlined in the SQL, not a bound param
        assert "kb" not in params

    def test_offset_path_contains_predicate(self) -> None:
        """Paginated (offset > 0) path must also carry the metadata predicate."""
        repo, pool = self._make_repo()
        repo.list_all(_SCOPE, offset=10, metadata_filter={"source": "support"})
        sql = pool.cursor.last_sql
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.source') = 'support'" in sql
        # Value is inlined, not a bound param
        assert "support" not in pool.cursor.last_params


# ---------------------------------------------------------------------------
# Tests: search() SQL and param integration
# ---------------------------------------------------------------------------

class TestSearchWithMetadataFilter:
    def _make_repo(
        self, rows: list[tuple[Any, ...]] | None = None
    ) -> tuple[SemanticFactRepository, _FakePool]:
        pool = _FakePool(rows)
        repo = SemanticFactRepository(pool)
        return repo, pool

    def test_no_filter_no_predicate(self) -> None:
        repo, pool = self._make_repo()
        repo.search(_VEC, _SCOPE, search_chunks=False)
        sql = pool.cursor.last_sql
        assert "JSON_VALUE" not in sql
        assert "JSON_EXISTS" not in sql
        assert "LOCATE" not in sql

    def test_exact_match_in_step1_sql(self) -> None:
        """Step-1 (ID-ranking) SQL must contain the metadata predicate."""
        repo, pool = self._make_repo()
        repo.search(
            _VEC,
            _SCOPE,
            metadata_filter={"source": "support"},
            search_chunks=False,
        )
        # Step 1 SQL selects only id; it's the last_sql recorded by the cursor.
        sql = pool.cursor.last_sql
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.source') = 'support'" in sql
        assert "JSON_VALUE" in sql

    def test_exact_match_value_inlined_not_in_params(self) -> None:
        """Metadata filter values are inlined in SQL — no bound params emitted."""
        repo, pool = self._make_repo()
        repo.search(
            _VEC,
            _SCOPE,
            metadata_filter={"source": "support"},
            search_chunks=False,
        )
        # "support" is inlined in the SQL, not a bound param
        assert "support" not in pool.cursor.last_params

    def test_not_predicate_in_step1(self) -> None:
        repo, pool = self._make_repo()
        repo.search(
            _VEC,
            _SCOPE,
            metadata_filter={"status": {"$not": "archived"}},
            search_chunks=False,
        )
        sql = pool.cursor.last_sql
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.status') <> 'archived'" in sql
        assert "JSON_VALUE" in sql
        # Value is inlined, not a bound param
        assert "archived" not in pool.cursor.last_params

    def test_array_contains_in_step1(self) -> None:
        repo, pool = self._make_repo()
        repo.search(
            _VEC,
            _SCOPE,
            metadata_filter={"tags": {"$array_contains": "urgent"}},
            search_chunks=False,
        )
        sql = pool.cursor.last_sql
        assert "LOCATE" in sql
        assert "urgent" in sql

    def test_filter_combined_with_min_confidence(self) -> None:
        repo, pool = self._make_repo()
        repo.search(
            _VEC,
            _SCOPE,
            min_confidence=0.8,
            metadata_filter={"source": "kb"},
            search_chunks=False,
        )
        sql = pool.cursor.last_sql
        assert "confidence >= ?" in sql
        assert "JSON_VALUE(metadata FORMAT JSON, 'lax $.source') = 'kb'" in sql
        assert "JSON_VALUE" in sql
        params = pool.cursor.last_params
        assert 0.8 in params
        # "kb" is inlined in the SQL, not a bound param
        assert "kb" not in params


# ---------------------------------------------------------------------------
# Tests: error raised before SQL execution
# ---------------------------------------------------------------------------

class TestInvalidFilterRaisesBeforeSql:
    """InvalidMetadataFilterError must be raised before any SQL is executed."""

    def test_list_all_invalid_operator_raises(self) -> None:
        pool = _FakePool()
        repo = WorkingMemoryRepository(pool)
        with pytest.raises(InvalidMetadataFilterError):
            repo.list_all(_SCOPE, metadata_filter={"x": {"$unknown": "v"}})
        # No SQL should have been issued
        assert pool.cursor.last_sql == ""

    def test_search_invalid_field_name_raises(self) -> None:
        pool = _FakePool()
        repo = WorkingMemoryRepository(pool)
        with pytest.raises(InvalidMetadataFilterError):
            repo.search(_VEC, _SCOPE, metadata_filter={"bad;field": "v"}, search_chunks=False)
        assert pool.cursor.last_sql == ""


# ---------------------------------------------------------------------------
# Tests: exported from top-level package
# ---------------------------------------------------------------------------

def test_invalid_metadata_filter_error_exported() -> None:
    from agent_memory_sdk import InvalidMetadataFilterError as E

    assert issubclass(E, ValueError)


def test_invalid_metadata_filter_error_is_value_error() -> None:
    """InvalidMetadataFilterError must be a subclass of ValueError."""
    err = InvalidMetadataFilterError("test")
    assert isinstance(err, ValueError)

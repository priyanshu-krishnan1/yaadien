"""
tests/test_thrd1_messages.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for THRD-1: Message ingestion primitives

Coverage:
  1. add_messages() — basic ingest, IDs returned, role absorbed into metadata
  2. add_messages() — caller-supplied id is honoured
  3. get_messages() — chronological order (oldest first)
  4. get_messages() — start=1 slice
  5. get_messages() — end=2 slice
  6. get_messages() — combined start=1, end=3 slice
  7. delete_message() — returns 1 on success, 0 for unknown id
  8. delete_message() — soft-delete only (tombstone semantics, deleted_at set)

No live Db2 instance required — uses the fake-pool pattern from test_lifecycle.py.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from agent_memory_sdk.models import MemoryScope, WorkingMemory
from agent_memory_sdk.store import MemoryStore

# ---------------------------------------------------------------------------
# Fake DB infrastructure (same pattern as test_lifecycle.py / test_orc2.py)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
_VEC_STR = "[" + ",".join("0.1" for _ in range(1536)) + "]"
_SCOPE = MemoryScope(agent_id="agent-thrd1", tenant_id="t1")


def _content_hash(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.lower()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _working_row(
    id_: str,
    content: str = "msg",
    created_at: datetime = _NOW,
    deleted_at: Any = None,
    metadata: dict | None = None,
) -> tuple[Any, ...]:
    """Build a fake 16-column DB row for working_memory."""
    return (
        id_, "t1", "agent-thrd1", None, None,
        content, json.dumps(metadata or {}),
        _VEC_STR,
        1.0,
        _content_hash(content),
        created_at, created_at, None, 1, deleted_at,
        "DIRECT_WRITE",  # origin (TRU-1)
        None,  # consolidated_at (ENH-4)
    )


class _FakeCursor:
    """Minimal fake cursor: records SQL/params and returns preset rows."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows: list[tuple[Any, ...]] = rows or []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount: int = 1
        self.all_sqls: list[str] = []
        self.all_params: list[list[Any]] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self.last_sql = sql
        self.last_params = params or []
        self.all_sqls.append(sql)
        self.all_params.append(self.last_params)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass


class _FakePool:
    """Fake pool that always returns the same cursor (configurable rows)."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor = _FakeCursor(rows)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):
        yield self.conn


def _make_store(rows: list[tuple[Any, ...]] | None = None) -> tuple[MemoryStore, _FakePool]:
    """Return a (store, pool) pair backed by a fake pool."""
    pool = _FakePool(rows)
    store = MemoryStore(pool)
    return store, pool


# ---------------------------------------------------------------------------
# 1. add_messages() — basic ingest
# ---------------------------------------------------------------------------


class TestAddMessages:
    def test_returns_list_of_ids(self):
        """add_messages returns a list of string IDs, one per input message."""
        store, _ = _make_store()
        messages = [
            {"content": "Hello"},
            {"content": "World"},
        ]
        ids = store.add_messages(messages, _SCOPE)
        assert isinstance(ids, list)
        assert len(ids) == 2
        assert all(isinstance(i, str) for i in ids)

    def test_ids_are_unique(self):
        """Each returned ID is unique (two distinct messages → two distinct IDs)."""
        store, _ = _make_store()
        ids = store.add_messages(
            [{"content": "msg-a"}, {"content": "msg-b"}],
            _SCOPE,
        )
        assert ids[0] != ids[1]

    def test_role_absorbed_into_metadata(self):
        """Keys other than 'content' / 'id' / 'metadata' go into metadata."""
        store, pool = _make_store()
        store.add_messages(
            [{"role": "user", "content": "Hello from user"}],
            _SCOPE,
        )
        # The INSERT SQL should have the role serialised inside the metadata JSON.
        insert_sqls = [s for s in pool.cursor.all_sqls if "INSERT INTO working_memory" in s]
        assert insert_sqls, "Expected at least one INSERT INTO working_memory"
        # The params for the last INSERT contain the metadata JSON at index 6
        insert_params = [
            p for s, p in zip(pool.cursor.all_sqls, pool.cursor.all_params, strict=False)
            if "INSERT INTO working_memory" in s
        ]
        metadata_json = insert_params[-1][6]  # index 6 = metadata column
        metadata = json.loads(metadata_json)
        assert metadata.get("role") == "user"

    def test_explicit_metadata_key_merged(self):
        """'metadata' dict in the message dict is merged into the stored metadata."""
        store, pool = _make_store()
        store.add_messages(
            [{"content": "hi", "metadata": {"source": "api"}, "role": "assistant"}],
            _SCOPE,
        )
        insert_params = [
            p for s, p in zip(pool.cursor.all_sqls, pool.cursor.all_params, strict=False)
            if "INSERT INTO working_memory" in s
        ]
        metadata = json.loads(insert_params[-1][6])
        assert metadata.get("source") == "api"
        assert metadata.get("role") == "assistant"

    def test_caller_dict_not_mutated(self):
        """add_messages must not mutate the caller's original dict."""
        store, _ = _make_store()
        original = {"role": "user", "content": "hello"}
        copy_before = dict(original)
        store.add_messages([original], _SCOPE)
        assert original == copy_before

    def test_returned_ids_match_stored_records(self):
        """IDs returned equal the IDs of the records that were inserted."""
        store, pool = _make_store()
        ids = store.add_messages(
            [{"content": "check id"}],
            _SCOPE,
        )
        # The INSERT params[0] is the record's id
        insert_params = [
            p for s, p in zip(pool.cursor.all_sqls, pool.cursor.all_params, strict=False)
            if "INSERT INTO working_memory" in s
        ]
        inserted_id = insert_params[-1][0]
        assert ids[0] == inserted_id


class TestAddMessagesCallerSuppliedId:
    def test_caller_supplied_id_is_honoured(self):
        """When a message dict has 'id', that exact value is used as the record id."""
        store, pool = _make_store()
        custom_id = "my-custom-uuid-1234"
        ids = store.add_messages(
            [{"id": custom_id, "content": "explicit id message"}],
            _SCOPE,
        )
        assert ids[0] == custom_id

        # Also verify the INSERT used the same id
        insert_params = [
            p for s, p in zip(pool.cursor.all_sqls, pool.cursor.all_params, strict=False)
            if "INSERT INTO working_memory" in s
        ]
        assert insert_params[-1][0] == custom_id


# ---------------------------------------------------------------------------
# 2. get_messages() — chronological ordering and slicing
# ---------------------------------------------------------------------------

# We pre-build three rows with distinct created_at timestamps so that
# list_all() (ORDER BY created_at DESC) returns them newest-first.
_T1 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)  # oldest
_T2 = datetime(2026, 9, 1, 10, 1, 0, tzinfo=timezone.utc)
_T3 = datetime(2026, 9, 1, 10, 2, 0, tzinfo=timezone.utc)  # newest

# DB returns newest-first: row3, row2, row1
_ROW1 = _working_row("id-1", content="first",  created_at=_T1)
_ROW2 = _working_row("id-2", content="second", created_at=_T2)
_ROW3 = _working_row("id-3", content="third",  created_at=_T3)
_ROWS_NEWEST_FIRST = [_ROW3, _ROW2, _ROW1]


class TestGetMessages:
    def test_returns_chronological_order(self):
        """get_messages returns oldest-first (reversed from list_all newest-first)."""
        store, _ = _make_store(rows=_ROWS_NEWEST_FIRST)
        msgs = store.get_messages(_SCOPE)
        assert [m.id for m in msgs] == ["id-1", "id-2", "id-3"]

    def test_returns_working_memory_instances(self):
        """Each element is a WorkingMemory instance."""
        store, _ = _make_store(rows=_ROWS_NEWEST_FIRST)
        msgs = store.get_messages(_SCOPE)
        assert all(isinstance(m, WorkingMemory) for m in msgs)

    def test_start_slices_correctly(self):
        """start=1 skips the oldest message (index 0 in chronological order)."""
        store, _ = _make_store(rows=_ROWS_NEWEST_FIRST)
        msgs = store.get_messages(_SCOPE, start=1)
        assert [m.id for m in msgs] == ["id-2", "id-3"]

    def test_end_slices_correctly(self):
        """end=2 returns only the first two chronological messages."""
        store, _ = _make_store(rows=_ROWS_NEWEST_FIRST)
        msgs = store.get_messages(_SCOPE, end=2)
        assert [m.id for m in msgs] == ["id-1", "id-2"]

    def test_start_and_end_combined(self):
        """start=1, end=3 returns indices 1 and 2 from the chronological list."""
        store, _ = _make_store(rows=_ROWS_NEWEST_FIRST)
        msgs = store.get_messages(_SCOPE, start=1, end=3)
        assert [m.id for m in msgs] == ["id-2", "id-3"]

    def test_empty_store_returns_empty_list(self):
        """When there are no working-memory rows, get_messages returns []."""
        store, _ = _make_store(rows=[])
        msgs = store.get_messages(_SCOPE)
        assert msgs == []

    def test_start_beyond_length_returns_empty(self):
        """start beyond the list length returns an empty list (Python slice semantics)."""
        store, _ = _make_store(rows=_ROWS_NEWEST_FIRST)
        msgs = store.get_messages(_SCOPE, start=100)
        assert msgs == []


# ---------------------------------------------------------------------------
# 3. delete_message() — soft-delete semantics
# ---------------------------------------------------------------------------


class TestDeleteMessage:
    def test_returns_1_on_success(self):
        """delete_message returns 1 when the row is found and tombstoned."""
        pool = _FakePool()
        pool.cursor.rowcount = 1  # UPDATE affected 1 row
        store = MemoryStore(pool)
        result = store.delete_message("msg-id-abc", _SCOPE)
        assert result == 1

    def test_returns_0_for_unknown_id(self):
        """delete_message returns 0 when the row is not found (wrong scope / deleted)."""
        pool = _FakePool()
        pool.cursor.rowcount = 0  # UPDATE affected 0 rows
        store = MemoryStore(pool)
        result = store.delete_message("non-existent-id", _SCOPE)
        assert result == 0

    def test_issues_update_not_hard_delete(self):
        """Soft-delete path: SQL must be an UPDATE, not a DELETE FROM."""
        pool = _FakePool()
        pool.cursor.rowcount = 1
        store = MemoryStore(pool)
        store.delete_message("soft-id", _SCOPE)

        executed_sqls = pool.cursor.all_sqls
        # At least one UPDATE was issued
        assert any("UPDATE working_memory" in s for s in executed_sqls)
        # No hard DELETE was issued against working_memory
        assert not any(
            "DELETE FROM working_memory" in s for s in executed_sqls
        )

    def test_sets_deleted_at(self):
        """The UPDATE SQL sets deleted_at (tombstone column)."""
        pool = _FakePool()
        pool.cursor.rowcount = 1
        store = MemoryStore(pool)
        store.delete_message("any-id", _SCOPE)

        update_sqls = [s for s in pool.cursor.all_sqls if "UPDATE working_memory" in s]
        assert update_sqls, "No UPDATE working_memory found"
        assert any("deleted_at" in s for s in update_sqls)

    def test_return_type_is_int(self):
        """delete_message always returns an int (not bool)."""
        pool = _FakePool()
        pool.cursor.rowcount = 1
        store = MemoryStore(pool)
        result = store.delete_message("id-x", _SCOPE)
        assert type(result) is int  # not bool — int specifically

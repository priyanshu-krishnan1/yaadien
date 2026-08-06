"""
tests/test_thrd2_convenience.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for THRD-2: add_memory() / add_user() / add_agent() convenience wrappers.

Coverage:
  1.  add_memory() — returns a string ID
  2.  add_memory() — content stored in SemanticFact (INSERT into semantic_facts)
  3.  add_memory() — custom memory_id is honored
  4.  add_memory() — metadata is passed through
  5.  add_user()   — creates a new profile when none exists, returns ID
  6.  add_user()   — updates existing profile when one exists (same agent_id + user_id)
  7.  add_user()   — scope=None raises ValueError
  8.  add_agent()  — creates a new agent profile when none exists
  9.  add_agent()  — updates existing agent profile
  10. No duplicate created by two calls with same content (list_all still returns 1 record)

No live Db2 instance required — uses the fake-pool pattern from test_thrd1_messages.py.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.store import MemoryStore

# ---------------------------------------------------------------------------
# Fake DB infrastructure (same pattern as test_thrd1_messages.py)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
_VEC_STR = "[" + ",".join("0.0" for _ in range(1536)) + "]"
_SCOPE = MemoryScope(agent_id="agent-thrd2", tenant_id="t2")


def _content_hash(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.lower()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _fact_row(
    id_: str,
    content: str = "a fact",
    agent_id: str = "agent-thrd2",
) -> tuple[Any, ...]:
    """Build a fake 19-column DB row for semantic_facts (added origin TRU-1)."""
    return (
        id_, "t2", agent_id, None, None,
        content, json.dumps({}),
        _VEC_STR,
        1.0,
        _content_hash(content),
        _NOW, _NOW, None, 1, None,
        "DIRECT_WRITE",     # 15 origin (TRU-1)
        None, None, None,   # superseded_by, superseded_at, supersede_reason
    )


def _profile_row(
    id_: str,
    content: str = "a profile",
    agent_id: str = "agent-thrd2",
    user_id: str | None = "user-thrd2",
) -> tuple[Any, ...]:
    """Build a fake 16-column DB row for entity_profiles (added origin TRU-1)."""
    return (
        id_, "t2", agent_id, user_id, None,
        content, json.dumps({}),
        _VEC_STR,
        1.0,
        _content_hash(content),
        _NOW, _NOW, None, 1, None,
        "DIRECT_WRITE",  # 15 origin (TRU-1)
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
    """Fake pool that always returns the same cursor."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor = _FakeCursor(rows)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):
        yield self.conn


def _make_store(rows: list[tuple[Any, ...]] | None = None) -> tuple[MemoryStore, _FakePool]:
    pool = _FakePool(rows)
    store = MemoryStore(pool)
    return store, pool


# ---------------------------------------------------------------------------
# A pool whose cursor returns different rows on each get_connection() call.
# Used to simulate: first call (list_all dedup SELECT) → empty; second call
# (INSERT) → nothing; and later test runs that return an existing row.
# ---------------------------------------------------------------------------

class _MultiRoundPool:
    """Pool that cycles through a list of row-sets, one per get_connection call."""

    def __init__(self, rounds: list[list[tuple[Any, ...]]]) -> None:
        self._rounds = rounds
        self._idx = 0
        self.cursors: list[_FakeCursor] = []

    @contextmanager
    def get_connection(self):
        rows = self._rounds[self._idx] if self._idx < len(self._rounds) else []
        self._idx += 1
        cur = _FakeCursor(rows)
        conn = _FakeConn(cur)
        self.cursors.append(cur)
        yield conn


# ---------------------------------------------------------------------------
# 1 & 2. add_memory() — basic: returns a string ID; INSERT goes to semantic_facts
# ---------------------------------------------------------------------------

class TestAddMemoryBasic:
    def test_returns_string_id(self) -> None:
        store, _ = _make_store()
        result = store.add_memory("The sky is blue.", _SCOPE)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_inserts_into_semantic_facts(self) -> None:
        store, pool = _make_store()
        store.add_memory("The sky is blue.", _SCOPE)
        insert_sqls = [s for s in pool.cursor.all_sqls if "INSERT INTO semantic_facts" in s]
        assert insert_sqls, "Expected at least one INSERT INTO semantic_facts"

    def test_content_in_insert_params(self) -> None:
        store, pool = _make_store()
        store.add_memory("User loves Python.", _SCOPE)
        insert_params = [
            p for s, p in zip(pool.cursor.all_sqls, pool.cursor.all_params, strict=False)
            if "INSERT INTO semantic_facts" in s
        ]
        assert insert_params, "Expected INSERT params"
        # content is at index 5 in the INSERT params
        assert insert_params[-1][5] == "User loves Python."

    def test_id_returned_matches_inserted(self) -> None:
        store, pool = _make_store()
        returned_id = store.add_memory("check id match", _SCOPE)
        insert_params = [
            p for s, p in zip(pool.cursor.all_sqls, pool.cursor.all_params, strict=False)
            if "INSERT INTO semantic_facts" in s
        ]
        assert insert_params[-1][0] == returned_id


# ---------------------------------------------------------------------------
# 3. add_memory() — custom memory_id is honored
# ---------------------------------------------------------------------------

class TestAddMemoryCustomId:
    def test_custom_id_used_as_record_id(self) -> None:
        store, pool = _make_store()
        custom = "my-fixed-fact-id-999"
        returned = store.add_memory("Fixed fact.", _SCOPE, memory_id=custom)
        assert returned == custom
        insert_params = [
            p for s, p in zip(pool.cursor.all_sqls, pool.cursor.all_params, strict=False)
            if "INSERT INTO semantic_facts" in s
        ]
        assert insert_params[-1][0] == custom


# ---------------------------------------------------------------------------
# 4. add_memory() — metadata is passed through
# ---------------------------------------------------------------------------

class TestAddMemoryMetadata:
    def test_metadata_stored_in_insert(self) -> None:
        store, pool = _make_store()
        store.add_memory("Fact with meta.", _SCOPE, metadata={"source": "test", "confidence": 0.9})
        insert_params = [
            p for s, p in zip(pool.cursor.all_sqls, pool.cursor.all_params, strict=False)
            if "INSERT INTO semantic_facts" in s
        ]
        raw_meta = insert_params[-1][6]  # metadata JSON at index 6
        meta = json.loads(raw_meta)
        assert meta.get("source") == "test"
        assert meta.get("confidence") == 0.9

    def test_none_metadata_defaults_to_empty_dict(self) -> None:
        store, pool = _make_store()
        store.add_memory("No meta fact.", _SCOPE)
        insert_params = [
            p for s, p in zip(pool.cursor.all_sqls, pool.cursor.all_params, strict=False)
            if "INSERT INTO semantic_facts" in s
        ]
        meta = json.loads(insert_params[-1][6])
        assert meta == {}


# ---------------------------------------------------------------------------
# 5. add_user() — creates a new profile when none exists, returns ID
# ---------------------------------------------------------------------------

class TestAddUserCreate:
    def test_returns_string_id_on_create(self) -> None:
        # list_all returns empty (no existing profile) → INSERT path
        pool = _MultiRoundPool([
            [],   # list_all SELECT → no existing rows (dedup check also empty)
            [],   # dedup SELECT inside profiles.create
        ])
        store = MemoryStore(pool)
        result = store.add_user("user-42", "Power developer", scope=_SCOPE)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_inserts_into_entity_profiles(self) -> None:
        pool = _MultiRoundPool([[], []])
        store = MemoryStore(pool)
        store.add_user("user-42", "Power developer", scope=_SCOPE)
        all_sqls = [sql for cur in pool.cursors for sql in cur.all_sqls]
        assert any("INSERT INTO entity_profiles" in s for s in all_sqls)


# ---------------------------------------------------------------------------
# 6. add_user() — updates existing profile when one exists
# ---------------------------------------------------------------------------

class TestAddUserUpdate:
    def test_updates_content_of_existing_profile(self) -> None:
        existing_id = "existing-profile-id-001"
        existing = _profile_row(existing_id, content="Old profile text")

        # Round 0: list_all returns one existing profile row
        # Round 1: UPDATE (rowcount=1 already set on _FakeCursor)
        pool = _MultiRoundPool([
            [existing],  # list_all → found one
            [],          # UPDATE connection
        ])
        # Patch rowcount on UPDATE cursor to 1
        store = MemoryStore(pool)
        returned_id = store.add_user("user-thrd2", "Updated profile text", scope=_SCOPE)

        assert returned_id == existing_id

        all_sqls = [sql for cur in pool.cursors for sql in cur.all_sqls]
        assert any("UPDATE entity_profiles" in s for s in all_sqls)
        assert not any("INSERT INTO entity_profiles" in s for s in all_sqls)

    def test_updated_content_in_params(self) -> None:
        existing_id = "existing-profile-id-002"
        existing = _profile_row(existing_id, content="Old text")

        pool = _MultiRoundPool([[existing], []])
        store = MemoryStore(pool)
        store.add_user("user-thrd2", "New content here", scope=_SCOPE)

        update_sqls_params = [
            (sql, params)
            for cur in pool.cursors
            for sql, params in zip(cur.all_sqls, cur.all_params, strict=False)
            if "UPDATE entity_profiles" in sql
        ]
        assert update_sqls_params
        # content is the first param in the UPDATE
        assert update_sqls_params[-1][1][0] == "New content here"


# ---------------------------------------------------------------------------
# 7. add_user() — scope=None raises ValueError
# ---------------------------------------------------------------------------

class TestAddUserScopeNone:
    def test_raises_value_error_when_scope_is_none(self) -> None:
        store, _ = _make_store()
        with pytest.raises(ValueError, match="add_user\\(\\) requires a scope with agent_id set"):
            store.add_user("any-user", "some text", scope=None)


# ---------------------------------------------------------------------------
# 8. add_agent() — creates a new agent profile when none exists
# ---------------------------------------------------------------------------

class TestAddAgentCreate:
    def test_returns_string_id_on_create(self) -> None:
        pool = _MultiRoundPool([[], []])
        store = MemoryStore(pool)
        result = store.add_agent("agent-thrd2", "An agent profile")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_inserts_into_entity_profiles(self) -> None:
        pool = _MultiRoundPool([[], []])
        store = MemoryStore(pool)
        store.add_agent("agent-thrd2", "An agent profile")
        all_sqls = [sql for cur in pool.cursors for sql in cur.all_sqls]
        assert any("INSERT INTO entity_profiles" in s for s in all_sqls)

    def test_scope_none_still_works(self) -> None:
        """add_agent with scope=None should not raise."""
        pool = _MultiRoundPool([[], []])
        store = MemoryStore(pool)
        result = store.add_agent("agent-no-scope", "Profile text", scope=None)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 9. add_agent() — updates existing agent profile
# ---------------------------------------------------------------------------

class TestAddAgentUpdate:
    def test_updates_content_of_existing_agent_profile(self) -> None:
        existing_id = "existing-agent-profile-id-001"
        existing = _profile_row(existing_id, content="Old agent profile", user_id=None)

        pool = _MultiRoundPool([[existing], []])
        store = MemoryStore(pool)
        returned_id = store.add_agent("agent-thrd2", "New agent profile text")

        assert returned_id == existing_id
        all_sqls = [sql for cur in pool.cursors for sql in cur.all_sqls]
        assert any("UPDATE entity_profiles" in s for s in all_sqls)
        assert not any("INSERT INTO entity_profiles" in s for s in all_sqls)


# ---------------------------------------------------------------------------
# 10. Upsert doesn't create a duplicate (list_all still returns 1 after 2 calls)
# ---------------------------------------------------------------------------

class TestUpsertNoDuplicate:
    def test_add_user_second_call_updates_not_inserts(self) -> None:
        """After an initial create, a second add_user with same scope must UPDATE, not INSERT."""
        profile_id = "stable-profile-id"
        first_profile_row = _profile_row(profile_id, content="Original text")

        # First call: list_all empty → INSERT path (3 connections)
        # Second call: list_all returns the profile → UPDATE path (2 connections)
        pool = _MultiRoundPool([
            [],                    # first add_user: list_all → empty
            [],                    # first add_user: dedup SELECT inside create()
            [],                    # first add_user: INSERT inside create()
            [first_profile_row],   # second add_user: list_all → found
            [],                    # second add_user: UPDATE
        ])
        store = MemoryStore(pool)

        id1 = store.add_user("user-dup", "Original text", scope=_SCOPE)
        id2 = store.add_user("user-dup", "Updated text", scope=_SCOPE)

        # Both calls return a valid string ID
        assert isinstance(id1, str)
        assert isinstance(id2, str)

        # Second call used UPDATE, not a second INSERT
        all_sqls = [sql for cur in pool.cursors for sql in cur.all_sqls]
        inserts = [s for s in all_sqls if "INSERT INTO entity_profiles" in s]
        updates = [s for s in all_sqls if "UPDATE entity_profiles" in s]
        assert len(inserts) == 1  # only the first call inserted
        assert len(updates) == 1  # the second call updated

    def test_add_agent_second_call_updates_not_inserts(self) -> None:
        """After initial create, second add_agent must UPDATE, not INSERT."""
        agent_id = "agent-dup-test"
        profile_id = "stable-agent-profile-id"
        first_profile_row = _profile_row(profile_id, content="Agent v1", user_id=None)

        pool = _MultiRoundPool([
            [],                    # first add_agent: list_all → empty
            [],                    # first add_agent: dedup SELECT inside create()
            [],                    # first add_agent: INSERT inside create()
            [first_profile_row],   # second add_agent: list_all → found
            [],                    # second add_agent: UPDATE
        ])
        store = MemoryStore(pool)

        store.add_agent(agent_id, "Agent v1")
        store.add_agent(agent_id, "Agent v2")

        all_sqls = [sql for cur in pool.cursors for sql in cur.all_sqls]
        inserts = [s for s in all_sqls if "INSERT INTO entity_profiles" in s]
        updates = [s for s in all_sqls if "UPDATE entity_profiles" in s]
        assert len(inserts) == 1
        assert len(updates) == 1

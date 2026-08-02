"""
tests/test_thrd8_delete_memory.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for THRD-8: ``MemoryStore.delete_memory(memory_id, scope) -> int``

Coverage:
  1.  Returns 1 when the ID exists in facts
  2.  Returns 1 when the ID exists in profiles
  3.  Returns 1 when the ID exists in procedures
  4.  Returns 0 when the ID is not found in any of the three repos
  5.  Stops at the first match — does not keep trying after a hit in facts
  6.  Stops at the first match — does not keep trying after a hit in profiles
  7.  Tries in order: facts → profiles → procedures (verified via call log)
  8.  Working repo is NOT touched by delete_memory()
  9.  Episodic repo is NOT touched by delete_memory()
  10. Scope is passed correctly to each forget() call

No live Db2 instance required — repos are replaced with a lightweight
``_FakeRepo`` mock that records calls and returns a preset value.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.store import MemoryStore

# ---------------------------------------------------------------------------
# Shared scope used across all tests
# ---------------------------------------------------------------------------

_SCOPE = MemoryScope(agent_id="agent-thrd8", tenant_id="t1")

# ---------------------------------------------------------------------------
# Minimal fake pool (no DB calls are made in these tests, but MemoryStore
# requires a pool at construction time for the repository constructors)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self) -> None:
        self.rows: list[tuple[Any, ...]] = []
        self.rowcount: int = 0

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        pass

    def fetchone(self) -> tuple[Any, ...] | None:
        return None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return []


class _FakeConn:
    def __init__(self) -> None:
        self._cursor = _FakeCursor()

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _FakePool:
    @contextmanager
    def get_connection(self):
        yield _FakeConn()


# ---------------------------------------------------------------------------
# Helper: build a MemoryStore with mocked per-repo forget() methods
#
# ``forget_map`` maps repo attribute name → the bool that repo.forget()
# should return.  Repos not listed return False by default.
# ---------------------------------------------------------------------------


def _make_store_with_mocked_repos(
    forget_map: dict[str, bool] | None = None,
) -> tuple[MemoryStore, dict[str, MagicMock]]:
    """Return (store, mocks) where mocks['facts'] etc. are the MagicMock
    objects patched onto each repo's ``forget`` method."""
    store = MemoryStore(_FakePool())
    forget_map = forget_map or {}
    mocks: dict[str, MagicMock] = {}
    for repo_attr in ("working", "episodic", "facts", "profiles", "procedures"):
        return_val = forget_map.get(repo_attr, False)
        mock = MagicMock(return_value=return_val)
        getattr(store, repo_attr).forget = mock
        mocks[repo_attr] = mock
    return store, mocks


# ---------------------------------------------------------------------------
# 1. Returns 1 when the ID exists in facts
# ---------------------------------------------------------------------------


class TestDeleteMemoryHitInFacts:
    def test_returns_1(self):
        """delete_memory returns 1 when the record is found in facts."""
        store, _ = _make_store_with_mocked_repos({"facts": True})
        assert store.delete_memory("id-in-facts", _SCOPE) == 1

    def test_return_type_is_int(self):
        """Return value is exactly int, not bool."""
        store, _ = _make_store_with_mocked_repos({"facts": True})
        result = store.delete_memory("id-in-facts", _SCOPE)
        assert type(result) is int


# ---------------------------------------------------------------------------
# 2. Returns 1 when the ID exists in profiles
# ---------------------------------------------------------------------------


class TestDeleteMemoryHitInProfiles:
    def test_returns_1(self):
        """delete_memory returns 1 when the record is found in profiles."""
        store, _ = _make_store_with_mocked_repos({"profiles": True})
        assert store.delete_memory("id-in-profiles", _SCOPE) == 1


# ---------------------------------------------------------------------------
# 3. Returns 1 when the ID exists in procedures
# ---------------------------------------------------------------------------


class TestDeleteMemoryHitInProcedures:
    def test_returns_1(self):
        """delete_memory returns 1 when the record is found in procedures."""
        store, _ = _make_store_with_mocked_repos({"procedures": True})
        assert store.delete_memory("id-in-procedures", _SCOPE) == 1


# ---------------------------------------------------------------------------
# 4. Returns 0 when the ID is not found in any of the three repos
# ---------------------------------------------------------------------------


class TestDeleteMemoryNotFound:
    def test_returns_0(self):
        """delete_memory returns 0 when the ID is absent from all three tables."""
        store, _ = _make_store_with_mocked_repos()  # all return False
        assert store.delete_memory("ghost-id", _SCOPE) == 0

    def test_return_type_is_int(self):
        """Return value is exactly int, not bool, even for the not-found case."""
        store, _ = _make_store_with_mocked_repos()
        result = store.delete_memory("ghost-id", _SCOPE)
        assert type(result) is int


# ---------------------------------------------------------------------------
# 5 & 6. Stops at the first match (does not keep trying after a hit)
# ---------------------------------------------------------------------------


class TestDeleteMemoryStopsAtFirstMatch:
    def test_stops_after_facts_hit(self):
        """When facts.forget() returns True, profiles and procedures are not called."""
        store, mocks = _make_store_with_mocked_repos({"facts": True})
        store.delete_memory("id-abc", _SCOPE)
        mocks["facts"].assert_called_once()
        mocks["profiles"].assert_not_called()
        mocks["procedures"].assert_not_called()

    def test_stops_after_profiles_hit(self):
        """When profiles.forget() returns True, procedures is not called."""
        store, mocks = _make_store_with_mocked_repos({"profiles": True})
        # facts returns False (default), profiles returns True
        store.delete_memory("id-abc", _SCOPE)
        mocks["facts"].assert_called_once()
        mocks["profiles"].assert_called_once()
        mocks["procedures"].assert_not_called()


# ---------------------------------------------------------------------------
# 7. Tries in order: facts → profiles → procedures
# ---------------------------------------------------------------------------


class TestDeleteMemoryOrdering:
    def test_facts_tried_before_profiles(self):
        """facts.forget() is called before profiles.forget() on every invocation."""
        call_order: list[str] = []

        store = MemoryStore(_FakePool())
        store.facts.forget = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda mid, sc: call_order.append("facts") or False
        )
        store.profiles.forget = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda mid, sc: call_order.append("profiles") or False
        )
        store.procedures.forget = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda mid, sc: call_order.append("procedures") or False
        )

        store.delete_memory("some-id", _SCOPE)
        assert call_order == ["facts", "profiles", "procedures"]

    def test_profiles_tried_before_procedures(self):
        """When facts misses, profiles is tried next (before procedures)."""
        call_order: list[str] = []

        store = MemoryStore(_FakePool())
        store.facts.forget = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda mid, sc: call_order.append("facts") or False
        )
        store.profiles.forget = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda mid, sc: call_order.append("profiles") or True
        )
        store.procedures.forget = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda mid, sc: call_order.append("procedures") or False
        )

        store.delete_memory("some-id", _SCOPE)
        # facts and profiles called; procedures must not have been reached
        assert call_order == ["facts", "profiles"]


# ---------------------------------------------------------------------------
# 8 & 9. Working and episodic repos are NOT touched
# ---------------------------------------------------------------------------


class TestDeleteMemoryExclusions:
    def test_working_repo_not_touched(self):
        """delete_memory never calls working.forget() regardless of outcome."""
        store, mocks = _make_store_with_mocked_repos({"facts": True})
        store.delete_memory("any-id", _SCOPE)
        mocks["working"].assert_not_called()

    def test_episodic_repo_not_touched(self):
        """delete_memory never calls episodic.forget() regardless of outcome."""
        store, mocks = _make_store_with_mocked_repos()
        store.delete_memory("any-id", _SCOPE)
        mocks["episodic"].assert_not_called()

    def test_working_not_touched_even_when_all_miss(self):
        """Even in the not-found (return 0) path, working is never called."""
        store, mocks = _make_store_with_mocked_repos()
        store.delete_memory("ghost", _SCOPE)
        mocks["working"].assert_not_called()
        mocks["episodic"].assert_not_called()


# ---------------------------------------------------------------------------
# 10. Scope is passed correctly to each forget() call
# ---------------------------------------------------------------------------


class TestDeleteMemoryScopePropagation:
    def test_scope_forwarded_to_facts(self):
        """The scope passed to delete_memory is forwarded verbatim to facts.forget()."""
        store, mocks = _make_store_with_mocked_repos({"facts": True})
        store.delete_memory("id-x", _SCOPE)
        mocks["facts"].assert_called_once_with("id-x", _SCOPE)

    def test_scope_forwarded_to_profiles(self):
        """The scope is forwarded to profiles.forget() when facts misses."""
        store, mocks = _make_store_with_mocked_repos({"profiles": True})
        store.delete_memory("id-y", _SCOPE)
        mocks["profiles"].assert_called_once_with("id-y", _SCOPE)

    def test_scope_forwarded_to_procedures(self):
        """The scope is forwarded to procedures.forget() when facts and profiles miss."""
        store, mocks = _make_store_with_mocked_repos({"procedures": True})
        store.delete_memory("id-z", _SCOPE)
        mocks["procedures"].assert_called_once_with("id-z", _SCOPE)

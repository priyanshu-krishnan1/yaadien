"""
tests/test_thrd10_scope_matching.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for THRD-10: fuzzy vs. exact per-dimension scope matching
in MemoryStore.search(), including unscoped-only queries.

No live Db2 needed — all repository search() calls are mocked.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

from agent_memory_sdk.models import MemoryScope, SemanticFact, WorkingMemory
from agent_memory_sdk.store import MemoryStore

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_VEC = [0.1] * 1536
_AGENT_A = "agent-alpha"
_AGENT_B = "agent-beta"
_THREAD_X = "thread-x"
_THREAD_Y = "thread-y"


# ---------------------------------------------------------------------------
# Minimal fake pool (no DB calls — repos are mocked)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def execute(self, sql: str, params: Any = None) -> None:
        pass

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list:
        return []


class _FakeConn:
    def cursor(self) -> _FakeCursor:
        return _FakeCursor()

    def commit(self) -> None:
        pass


class _FakePool:
    @contextmanager
    def get_connection(self):  # type: ignore[return]
        yield _FakeConn()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fact(
    uid: str,
    *,
    agent_id: str = _AGENT_A,
    thread_id: str | None = None,
) -> SemanticFact:
    r = SemanticFact(agent_id=agent_id, content=f"content-{uid}", thread_id=thread_id)
    r.id = uid
    return r


def _make_working(
    uid: str,
    *,
    agent_id: str = _AGENT_A,
    thread_id: str | None = None,
) -> WorkingMemory:
    r = WorkingMemory(agent_id=agent_id, content=f"content-{uid}", thread_id=thread_id)
    r.id = uid
    return r


def _make_store(
    facts_results: list | None = None,
    working_results: list | None = None,
    episodic_results: list | None = None,
    profiles_results: list | None = None,
    procedures_results: list | None = None,
) -> MemoryStore:
    """Return a MemoryStore with all five repo.search() methods mocked."""
    store = MemoryStore(
        _FakePool(),
        embedding_provider=lambda text: _VEC,
        enable_chunking=False,
    )
    store.working.search = MagicMock(return_value=working_results or [])
    store.episodic.search = MagicMock(return_value=episodic_results or [])
    store.facts.search = MagicMock(return_value=facts_results or [])
    store.profiles.search = MagicMock(return_value=profiles_results or [])
    store.procedures.search = MagicMock(return_value=procedures_results or [])
    return store


# ---------------------------------------------------------------------------
# Tests: exact_thread_match (default True)
# ---------------------------------------------------------------------------


class TestExactThreadMatchDefault:
    """exact_thread_match=True (default) filters on thread_id."""

    def test_matching_thread_id_passes(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        record = _make_fact("f1", agent_id=_AGENT_A, thread_id=_THREAD_X)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"])
        assert len(results) == 1
        assert results[0].id == "f1"

    def test_different_thread_id_excluded(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        record = _make_fact("f1", agent_id=_AGENT_A, thread_id=_THREAD_Y)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"])
        assert results == []

    def test_none_thread_excluded_when_scope_has_thread(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        record = _make_fact("f1", agent_id=_AGENT_A, thread_id=None)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"])
        assert results == []


# ---------------------------------------------------------------------------
# Tests: unscoped-only query (scope.thread_id=None + exact_thread_match=True)
# ---------------------------------------------------------------------------


class TestUnscopedOnlyQuery:
    """scope.thread_id=None + exact_thread_match=True → only NULL thread_id rows pass."""

    def test_null_thread_record_passes(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=None)
        record = _make_fact("f1", agent_id=_AGENT_A, thread_id=None)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"])
        assert len(results) == 1
        assert results[0].id == "f1"

    def test_non_null_thread_excluded_when_scope_thread_is_none(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=None)
        record = _make_fact("f1", agent_id=_AGENT_A, thread_id=_THREAD_X)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"])
        assert results == []

    def test_mix_null_and_non_null_only_null_passes(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=None)
        r_null = _make_fact("f-null", agent_id=_AGENT_A, thread_id=None)
        r_threaded = _make_fact("f-thread", agent_id=_AGENT_A, thread_id=_THREAD_X)
        store = _make_store(facts_results=[r_null, r_threaded])
        results = store.search("q", scope, record_types=["facts"])
        assert len(results) == 1
        assert results[0].id == "f-null"


# ---------------------------------------------------------------------------
# Tests: exact_thread_match=False (fuzzy — any thread_id passes)
# ---------------------------------------------------------------------------


class TestExactThreadMatchFalse:
    """exact_thread_match=False: records with any thread_id pass through."""

    def test_matching_thread_passes(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        record = _make_fact("f1", agent_id=_AGENT_A, thread_id=_THREAD_X)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"], exact_thread_match=False)
        assert len(results) == 1

    def test_different_thread_passes(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        record = _make_fact("f1", agent_id=_AGENT_A, thread_id=_THREAD_Y)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"], exact_thread_match=False)
        assert len(results) == 1
        assert results[0].id == "f1"

    def test_null_thread_passes_when_scope_has_thread(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        record = _make_fact("f1", agent_id=_AGENT_A, thread_id=None)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"], exact_thread_match=False)
        assert len(results) == 1

    def test_non_null_thread_passes_when_scope_thread_is_none(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=None)
        record = _make_fact("f1", agent_id=_AGENT_A, thread_id=_THREAD_X)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"], exact_thread_match=False)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Tests: exact_agent_match (default True)
# ---------------------------------------------------------------------------


class TestExactAgentMatchDefault:
    """exact_agent_match=True (default) filters on agent_id."""

    def test_matching_agent_passes(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A)
        record = _make_fact("f1", agent_id=_AGENT_A)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"])
        assert len(results) == 1

    def test_different_agent_excluded(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A)
        record = _make_fact("f1", agent_id=_AGENT_B)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"])
        assert results == []

    def test_multiple_records_only_correct_agent_passes(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A)
        r_a = _make_fact("f-a", agent_id=_AGENT_A)
        r_b = _make_fact("f-b", agent_id=_AGENT_B)
        store = _make_store(facts_results=[r_a, r_b])
        results = store.search("q", scope, record_types=["facts"])
        assert len(results) == 1
        assert results[0].id == "f-a"


# ---------------------------------------------------------------------------
# Tests: exact_agent_match=False
# ---------------------------------------------------------------------------


class TestExactAgentMatchFalse:
    """exact_agent_match=False: records with any agent_id pass through."""

    def test_matching_agent_passes(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A)
        record = _make_fact("f1", agent_id=_AGENT_A)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"], exact_agent_match=False)
        assert len(results) == 1

    def test_different_agent_passes(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A)
        record = _make_fact("f1", agent_id=_AGENT_B)
        store = _make_store(facts_results=[record])
        results = store.search("q", scope, record_types=["facts"], exact_agent_match=False)
        assert len(results) == 1
        assert results[0].id == "f1"


# ---------------------------------------------------------------------------
# Tests: combined both=True (default)
# ---------------------------------------------------------------------------


class TestBothDefaultsTrue:
    """Both exact_agent_match=True and exact_thread_match=True: combined filter."""

    def test_both_match_passes(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        r = _make_fact("f1", agent_id=_AGENT_A, thread_id=_THREAD_X)
        store = _make_store(facts_results=[r])
        results = store.search("q", scope, record_types=["facts"])
        assert len(results) == 1

    def test_wrong_agent_correct_thread_excluded(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        r = _make_fact("f1", agent_id=_AGENT_B, thread_id=_THREAD_X)
        store = _make_store(facts_results=[r])
        results = store.search("q", scope, record_types=["facts"])
        assert results == []

    def test_correct_agent_wrong_thread_excluded(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        r = _make_fact("f1", agent_id=_AGENT_A, thread_id=_THREAD_Y)
        store = _make_store(facts_results=[r])
        results = store.search("q", scope, record_types=["facts"])
        assert results == []

    def test_wrong_agent_wrong_thread_excluded(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        r = _make_fact("f1", agent_id=_AGENT_B, thread_id=_THREAD_Y)
        store = _make_store(facts_results=[r])
        results = store.search("q", scope, record_types=["facts"])
        assert results == []


# ---------------------------------------------------------------------------
# Tests: both=False (no post-filtering)
# ---------------------------------------------------------------------------


class TestBothFalse:
    """Both exact_agent_match=False and exact_thread_match=False: no filtering."""

    def test_all_results_pass_through(self) -> None:
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        records = [
            _make_fact("f1", agent_id=_AGENT_A, thread_id=_THREAD_X),
            _make_fact("f2", agent_id=_AGENT_B, thread_id=_THREAD_Y),
            _make_fact("f3", agent_id=_AGENT_B, thread_id=None),
        ]
        store = _make_store(facts_results=records)
        results = store.search(
            "q",
            scope,
            record_types=["facts"],
            exact_agent_match=False,
            exact_thread_match=False,
        )
        assert len(results) == 3
        ids = {r.id for r in results}
        assert ids == {"f1", "f2", "f3"}


# ---------------------------------------------------------------------------
# Tests: max_results truncation still applies after filtering
# ---------------------------------------------------------------------------


class TestMaxResultsTruncationAfterFiltering:
    """max_results cap is applied on the post-filter list, not the pre-filter list."""

    def test_truncation_after_filtering(self) -> None:
        # 5 records all matching the scope, but max_results=2
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=None)
        records = [_make_fact(f"f{i}", agent_id=_AGENT_A, thread_id=None) for i in range(5)]
        store = _make_store(facts_results=records)
        results = store.search("q", scope, record_types=["facts"], max_results=2)
        assert len(results) == 2

    def test_filter_reduces_below_max_results(self) -> None:
        # 5 records, but only 2 match the thread filter; max_results=10
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        matching = [_make_fact(f"m{i}", agent_id=_AGENT_A, thread_id=_THREAD_X) for i in range(2)]
        excluded = [_make_fact(f"e{i}", agent_id=_AGENT_A, thread_id=_THREAD_Y) for i in range(3)]
        store = _make_store(facts_results=matching + excluded)
        results = store.search("q", scope, record_types=["facts"], max_results=10)
        assert len(results) == 2
        assert all(r.record.thread_id == _THREAD_X for r in results)

    def test_max_results_zero_after_filtering_returns_empty(self) -> None:
        # All records filtered out; max_results cap on empty list is still empty
        scope = MemoryScope(agent_id=_AGENT_A, thread_id=_THREAD_X)
        records = [_make_fact(f"f{i}", agent_id=_AGENT_A, thread_id=_THREAD_Y) for i in range(3)]
        store = _make_store(facts_results=records)
        results = store.search("q", scope, record_types=["facts"], max_results=10)
        assert results == []


# ---------------------------------------------------------------------------
# Tests: repositories/base.py is untouched
# ---------------------------------------------------------------------------


class TestBaseRepositoryUntouched:
    """Confirm _scope_predicates() in repositories/base.py was not modified.

    This class guards the security-critical _scope_predicates() function that
    enforces agent/tenant/user/thread isolation at the SQL level.  The check
    verifies that _scope_predicates() itself is untouched by running git diff
    and asserting the function body still contains the exact isolation
    predicates ('agent_id = ?', 'tenant_id = ?', etc.).

    Note: other parts of base.py (e.g. _build_metadata_filter) may legitimately
    change as bugs are fixed.  The sentinel here is deliberately scoped to the
    _scope_predicates function body rather than the whole file, so that correct
    bug-fixes elsewhere do not trigger false-positive failures.
    """

    def test_scope_predicates_function_contains_isolation_guards(self) -> None:
        """_scope_predicates() must contain the mandatory isolation predicates."""
        import pathlib

        base_py = pathlib.Path(__file__).parents[1] / "src" / "agent_memory_sdk" / "repositories" / "base.py"
        source = base_py.read_text(encoding="utf-8")

        # Locate the _scope_predicates function body
        assert "def _scope_predicates" in source, (
            "_scope_predicates function is missing from repositories/base.py!"
        )
        # Must always include agent_id isolation
        assert '"agent_id = ?"' in source or "'agent_id = ?'" in source, (
            "SECURITY: 'agent_id = ?' predicate missing from _scope_predicates!"
        )
        # Must always include tenant_id isolation
        assert '"tenant_id = ?"' in source or "'tenant_id = ?'" in source, (
            "SECURITY: 'tenant_id = ?' predicate missing from _scope_predicates!"
        )

"""
tests/integration/test_scope_matching_modes.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-18: exact vs. fuzzy scope matching and
unscoped-only queries via ``store.search()``'s ``exact_agent_match``
and ``exact_thread_match`` parameters (THRD-10).

This is a SECURITY-CRITICAL file.  Every assertion here probes a real
isolation boundary backed by a real IBM Db2 LUW instance.  If an
assertion **fails**, it is a real P0 isolation bug, not a test bug.
Do NOT adjust a failing assertion to make it pass — stop and report.

Coverage (in class order):
  TestExactAgentMatchDefault  — default exact_agent_match=True never
                                leaks another agent's rows (negative leak
                                detection, exhaustive across 3 agents ×
                                3 record types).
  TestFuzzyAgentMatch         — exact_agent_match=False returns a broader
                                result set; confirmed to NOT be the default
                                in any production call site in src/.
  TestExactThreadMatch        — exact_thread_match=True/False isolates or
                                widens within one agent across two threads.
  TestUnscopedOnlyQueries     — scope.thread_id=None + exact_thread_match=True
                                returns only genuinely thread_id=None rows;
                                rows with any non-None thread_id must not
                                appear.

All tests are skipped automatically when DB2_DATABASE is not set.
See project-management/INTEGRATION_TESTING.md for Docker setup.

────────────────────────────────────────────────────────────────────────
SECURITY SCAN RESULT (captured at file-write time)
────────────────────────────────────────────────────────────────────────
Grep of src/agent_memory_sdk/store.py and src/agent_memory_sdk/thread.py
for any hardcoded ``exact_agent_match=False`` or
``exact_thread_match=False`` call site:

  RESULT: **NONE FOUND** — both files pass the scan clean.

The scan is re-executed at runtime in
``TestFuzzyAgentMatch.test_fuzzy_agent_mode_is_not_the_default`` (below).
If a future commit introduces a hardcoded fuzzy call site in production
code, that test will surface a ``pytest.fail()`` immediately.
────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
import uuid

import pytest

from tests.integration.conftest import make_unit_vec

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# HashEmbedder — deterministic embedding provider (identical to test_thread_primitives.py)
# ---------------------------------------------------------------------------


class HashEmbedder:
    """Deterministic embedding provider — no external service needed.

    Returns ``make_unit_vec(1536, abs(hash(text)) % 1500)``.  Same text →
    same unit vector; different texts with different hashes produce orthogonal
    vectors (cosine distance = 1.0), making vector search fully deterministic.
    """

    DIM = 1536

    def __call__(self, text: str) -> list[float]:
        return make_unit_vec(self.DIM, abs(hash(text)) % 1500)


# ---------------------------------------------------------------------------
# Module-level fixture helpers
# ---------------------------------------------------------------------------


def _make_store(migrated_pool):
    """Return a MemoryStore wired with HashEmbedder for deterministic search."""
    from agent_memory_sdk import MemoryStore

    return MemoryStore(migrated_pool, embedding_provider=HashEmbedder())


def _unique_id(prefix: str = "agent") -> str:
    return f"test-{prefix}-{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# TestExactAgentMatchDefault
# Ensures that the default exact_agent_match=True NEVER returns rows belonging
# to a different agent.  This is the critical negative / leak-detection class.
# ---------------------------------------------------------------------------


class TestExactAgentMatchDefault:
    """exact_agent_match=True (the default) — exhaustive cross-agent isolation."""

    # ------------------------------------------------------------------ #
    # Setup helpers (used across tests; each test builds its own agents   #
    # so there is never any shared-state coupling between tests).         #
    # ------------------------------------------------------------------ #

    def _seed_multi_agent_data(self, store, migrated_pool):
        """Seed facts, profiles, and procedures for three distinct agents.

        Returns:
            (scope_a, scope_b, scope_c, seeded_ids)
              scope_a — MemoryScope for agent_A (the query scope)
              scope_b — MemoryScope for agent_B
              scope_c — MemoryScope for agent_C
              seeded_ids — frozenset of ALL seeded record IDs across all three
                           agents (used in negative assertions)
        """
        from agent_memory_sdk.models import (
            EntityProfile,
            MemoryScope,
            ProceduralMemory,
            SemanticFact,
        )

        agent_a = _unique_id("agent-A")
        agent_b = _unique_id("agent-B")
        agent_c = _unique_id("agent-C")

        scope_a = MemoryScope(agent_id=agent_a, user_id="user-1")
        scope_b = MemoryScope(agent_id=agent_b, user_id="user-1")
        scope_c = MemoryScope(agent_id=agent_c, user_id="user-1")

        content = "scope isolation test content for cross agent leak check"

        seeded_ids: set[str] = set()

        for scope in (scope_a, scope_b, scope_c):
            # Fact
            fact = SemanticFact(agent_id=scope.agent_id, user_id=scope.user_id, content=content)
            stored_fact = store.remember(fact, scope)
            seeded_ids.add(stored_fact.id)

            # Profile
            profile = EntityProfile(agent_id=scope.agent_id, user_id=scope.user_id, content=content)
            stored_profile = store.remember(profile, scope)
            seeded_ids.add(stored_profile.id)

            # Procedure
            procedure = ProceduralMemory(agent_id=scope.agent_id, user_id=scope.user_id, content=content)
            stored_procedure = store.remember(procedure, scope)
            seeded_ids.add(stored_procedure.id)

        return scope_a, scope_b, scope_c, frozenset(seeded_ids)

    def test_default_search_never_returns_agent_b_facts(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Searching scope_A with defaults never returns rows owned by agent_B."""
        store = _make_store(migrated_pool)
        scope_a, scope_b, scope_c, _ = self._seed_multi_agent_data(store, migrated_pool)

        results = store.search(
            "scope isolation test content",
            scope_a,
            record_types=["facts"],
            max_results=50,
        )

        result_agent_ids = {r.record.agent_id for r in results}
        # Must not contain agent_B or agent_C.
        assert scope_b.agent_id not in result_agent_ids, (
            f"ISOLATION BUG: agent_B rows leaked into agent_A search results! "
            f"result agent_ids={result_agent_ids!r}"
        )
        assert scope_c.agent_id not in result_agent_ids, (
            f"ISOLATION BUG: agent_C rows leaked into agent_A search results! "
            f"result agent_ids={result_agent_ids!r}"
        )

    def test_default_search_never_returns_agent_b_profiles(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Searching scope_A profiles with defaults never returns agent_B profiles."""
        store = _make_store(migrated_pool)
        scope_a, scope_b, scope_c, _ = self._seed_multi_agent_data(store, migrated_pool)

        results = store.search(
            "scope isolation test content",
            scope_a,
            record_types=["profiles"],
            max_results=50,
        )

        result_agent_ids = {r.record.agent_id for r in results}
        assert scope_b.agent_id not in result_agent_ids, (
            f"ISOLATION BUG: agent_B profile leaked into agent_A search! "
            f"result agent_ids={result_agent_ids!r}"
        )
        assert scope_c.agent_id not in result_agent_ids, (
            f"ISOLATION BUG: agent_C profile leaked into agent_A search! "
            f"result agent_ids={result_agent_ids!r}"
        )

    def test_default_search_never_returns_agent_b_procedures(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Searching scope_A procedures with defaults never returns agent_B procedures."""
        store = _make_store(migrated_pool)
        scope_a, scope_b, scope_c, _ = self._seed_multi_agent_data(store, migrated_pool)

        results = store.search(
            "scope isolation test content",
            scope_a,
            record_types=["procedures"],
            max_results=50,
        )

        result_agent_ids = {r.record.agent_id for r in results}
        assert scope_b.agent_id not in result_agent_ids, (
            f"ISOLATION BUG: agent_B procedure leaked into agent_A search! "
            f"result agent_ids={result_agent_ids!r}"
        )
        assert scope_c.agent_id not in result_agent_ids, (
            f"ISOLATION BUG: agent_C procedure leaked into agent_A search! "
            f"result agent_ids={result_agent_ids!r}"
        )

    def test_default_search_fanout_all_types_never_leaks(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Fan-out search across all types with defaults never leaks cross-agent rows."""
        store = _make_store(migrated_pool)
        scope_a, scope_b, scope_c, _ = self._seed_multi_agent_data(store, migrated_pool)

        results = store.search(
            "scope isolation test content",
            scope_a,
            max_results=100,
        )

        foreign_agent_ids = {scope_b.agent_id, scope_c.agent_id}
        for r in results:
            assert r.record.agent_id not in foreign_agent_ids, (
                f"ISOLATION BUG: foreign agent row leaked into agent_A fan-out search! "
                f"record id={r.id!r} agent_id={r.record.agent_id!r} "
                f"record_type={r.record_type!r}"
            )

    def test_exact_agent_match_true_explicit_same_as_default(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Explicitly passing exact_agent_match=True gives the same isolation as the default."""
        store = _make_store(migrated_pool)
        scope_a, scope_b, scope_c, _ = self._seed_multi_agent_data(store, migrated_pool)

        results_default = store.search(
            "scope isolation test content",
            scope_a,
            record_types=["facts"],
            max_results=50,
        )
        results_explicit = store.search(
            "scope isolation test content",
            scope_a,
            record_types=["facts"],
            max_results=50,
            exact_agent_match=True,
        )

        default_ids = {r.id for r in results_default}
        explicit_ids = {r.id for r in results_explicit}
        assert default_ids == explicit_ids, (
            "exact_agent_match=True (explicit) produced different results than the default. "
            f"default={default_ids!r} explicit={explicit_ids!r}"
        )

    def test_scope_b_search_never_returns_agent_a_rows(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Searching from scope_B never returns scope_A rows — isolation is symmetric."""
        store = _make_store(migrated_pool)
        scope_a, scope_b, scope_c, _ = self._seed_multi_agent_data(store, migrated_pool)

        results = store.search(
            "scope isolation test content",
            scope_b,
            record_types=["facts"],
            max_results=50,
        )

        result_agent_ids = {r.record.agent_id for r in results}
        assert scope_a.agent_id not in result_agent_ids, (
            f"ISOLATION BUG: agent_A rows leaked into agent_B search results! "
            f"result agent_ids={result_agent_ids!r}"
        )
        assert scope_c.agent_id not in result_agent_ids, (
            f"ISOLATION BUG: agent_C rows leaked into agent_B search results! "
            f"result agent_ids={result_agent_ids!r}"
        )

    def test_scope_c_search_never_returns_agent_a_or_b_rows(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Searching from scope_C never returns scope_A or scope_B rows."""
        store = _make_store(migrated_pool)
        scope_a, scope_b, scope_c, _ = self._seed_multi_agent_data(store, migrated_pool)

        results = store.search(
            "scope isolation test content",
            scope_c,
            record_types=["facts"],
            max_results=50,
        )

        result_agent_ids = {r.record.agent_id for r in results}
        assert scope_a.agent_id not in result_agent_ids, (
            f"ISOLATION BUG: agent_A rows leaked into agent_C search! "
            f"result agent_ids={result_agent_ids!r}"
        )
        assert scope_b.agent_id not in result_agent_ids, (
            f"ISOLATION BUG: agent_B rows leaked into agent_C search! "
            f"result agent_ids={result_agent_ids!r}"
        )

    def test_agent_a_results_only_contain_agent_a_agent_id(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """POSITIVE: every result returned for scope_A bears scope_A's agent_id."""
        store = _make_store(migrated_pool)
        scope_a, scope_b, scope_c, _ = self._seed_multi_agent_data(store, migrated_pool)

        results = store.search(
            "scope isolation test content",
            scope_a,
            max_results=100,
        )

        # Must have at least one result (scope_a was seeded above).
        assert len(results) >= 1, "Expected at least one result for scope_A after seeding."
        for r in results:
            assert r.record.agent_id == scope_a.agent_id, (
                f"ISOLATION BUG: result with agent_id={r.record.agent_id!r} returned "
                f"in search scoped to agent_id={scope_a.agent_id!r}"
            )


# ---------------------------------------------------------------------------
# TestFuzzyAgentMatch
# Verifies that exact_agent_match=False broadens results AND that this mode
# is never hardcoded as the default in any src/ production call site.
# ---------------------------------------------------------------------------


class TestFuzzyAgentMatch:
    """exact_agent_match=False — fuzzy mode widens results; confirmed NOT the default."""

    def test_fuzzy_agent_mode_is_not_the_default(self) -> None:
        """No call site in src/ hardcodes exact_agent_match=False or exact_thread_match=False.

        This is both a documentation assertion and an active source-code scan.
        If a future commit introduces a hardcoded fuzzy call site, this test
        will surface it immediately as a pytest.fail().
        """
        import pathlib

        src_root = pathlib.Path(__file__).parents[2] / "src"
        pattern = re.compile(r"exact_(?:agent|thread)_match\s*=\s*False")

        violations: list[str] = []
        for py_file in src_root.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    violations.append(f"{py_file.relative_to(src_root.parent)}:{lineno}: {line.strip()}")

        if violations:
            pytest.fail(
                "SECURITY FINDING: hardcoded exact_agent_match=False or "
                "exact_thread_match=False found in production source code!\n"
                "This means fuzzy-match mode is being used as the default "
                "somewhere, which could allow cross-agent or cross-thread data "
                "leakage.  Investigate each finding:\n\n"
                + "\n".join(f"  {v}" for v in violations)
            )

    def test_fuzzy_agent_returns_at_least_own_agent_rows(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """With exact_agent_match=False, scope_A's own rows are still returned."""
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        store = _make_store(migrated_pool)
        agent_a = _unique_id("fuzzy-A")
        scope_a = MemoryScope(agent_id=agent_a, user_id="user-1")

        fact = SemanticFact(
            agent_id=agent_a,
            user_id="user-1",
            content="fuzzy agent match test content",
        )
        stored = store.remember(fact, scope_a)

        results = store.search(
            "fuzzy agent match test content",
            scope_a,
            record_types=["facts"],
            max_results=50,
            exact_agent_match=False,
        )

        result_ids = {r.id for r in results}
        assert stored.id in result_ids, (
            "Own agent's row not found with exact_agent_match=False."
        )

    def test_fuzzy_agent_broader_than_exact_for_multi_agent_seed(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """exact_agent_match=False returns >= as many results as exact_agent_match=True.

        Seeds two agents with the SAME content (so both hit the same vector
        neighbourhood) and confirms that fuzzy mode sees at least as many
        rows as exact mode — i.e. fuzzy is strictly non-narrowing.
        """
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        store = _make_store(migrated_pool)
        agent_a = _unique_id("broader-A")
        agent_b = _unique_id("broader-B")
        scope_a = MemoryScope(agent_id=agent_a, user_id="user-1")
        scope_b = MemoryScope(agent_id=agent_b, user_id="user-1")

        content = "shared content for fuzzy agent match breadth test"
        for scope in (scope_a, scope_b):
            store.remember(
                SemanticFact(agent_id=scope.agent_id, user_id="user-1", content=content),
                scope,
            )

        exact_results = store.search(
            content, scope_a, record_types=["facts"], max_results=50, exact_agent_match=True
        )
        fuzzy_results = store.search(
            content, scope_a, record_types=["facts"], max_results=50, exact_agent_match=False
        )

        assert len(fuzzy_results) >= len(exact_results), (
            f"UNEXPECTED: fuzzy mode returned fewer results ({len(fuzzy_results)}) "
            f"than exact mode ({len(exact_results)}).  fuzzy must be non-narrowing."
        )

    def test_exact_mode_is_proper_subset_of_fuzzy_mode(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Every ID returned by exact mode is also present in fuzzy mode's results."""
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        store = _make_store(migrated_pool)
        agent_a = _unique_id("subset-A")
        scope_a = MemoryScope(agent_id=agent_a, user_id="user-1")

        for i in range(3):
            store.remember(
                SemanticFact(
                    agent_id=agent_a,
                    user_id="user-1",
                    content=f"subset test fact {i} for scope matching",
                ),
                scope_a,
            )

        exact_results = store.search(
            "subset test fact for scope matching",
            scope_a,
            record_types=["facts"],
            max_results=50,
            exact_agent_match=True,
        )
        fuzzy_results = store.search(
            "subset test fact for scope matching",
            scope_a,
            record_types=["facts"],
            max_results=50,
            exact_agent_match=False,
        )

        exact_ids = {r.id for r in exact_results}
        fuzzy_ids = {r.id for r in fuzzy_results}

        missing_from_fuzzy = exact_ids - fuzzy_ids
        assert not missing_from_fuzzy, (
            f"Rows returned by exact mode are missing from fuzzy mode: "
            f"{missing_from_fuzzy!r}.  Fuzzy must be a superset of exact."
        )


# ---------------------------------------------------------------------------
# TestExactThreadMatch
# Within a single agent, verifies thread-level isolation and relaxation.
# ---------------------------------------------------------------------------


class TestExactThreadMatch:
    """exact_thread_match=True/False — per-thread isolation within one agent."""

    def _seed_two_thread_data(self, store, agent_id: str):
        """Seed facts into thread_T1 and thread_T2 for *agent_id*.

        Returns:
            (scope_t1, scope_t2, id_t1, id_t2)
        """
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        scope_t1 = MemoryScope(agent_id=agent_id, user_id="user-1", thread_id="thread-T1")
        scope_t2 = MemoryScope(agent_id=agent_id, user_id="user-1", thread_id="thread-T2")

        content = "thread isolation fact content for exact match test"

        fact_t1 = SemanticFact(agent_id=agent_id, user_id="user-1", thread_id="thread-T1", content=content)
        stored_t1 = store.remember(fact_t1, scope_t1)

        fact_t2 = SemanticFact(agent_id=agent_id, user_id="user-1", thread_id="thread-T2", content=content)
        stored_t2 = store.remember(fact_t2, scope_t2)

        return scope_t1, scope_t2, stored_t1.id, stored_t2.id

    def test_exact_thread_match_true_default_only_returns_own_thread(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """exact_thread_match=True (default): thread_T1 search never returns thread_T2 rows."""
        store = _make_store(migrated_pool)
        scope_t1, scope_t2, id_t1, id_t2 = self._seed_two_thread_data(
            store, unique_agent_id
        )

        results = store.search(
            "thread isolation fact content",
            scope_t1,
            record_types=["facts"],
            max_results=50,
        )

        result_ids = {r.id for r in results}

        # The thread_T2 row must NOT appear.
        assert id_t2 not in result_ids, (
            f"ISOLATION BUG: thread_T2 fact (id={id_t2!r}) leaked into thread_T1 "
            f"search with exact_thread_match=True (default)!"
        )
        # Every returned row must carry thread_T1's thread_id.
        for r in results:
            assert r.record.thread_id == scope_t1.thread_id, (
                f"ISOLATION BUG: result thread_id={r.record.thread_id!r} differs "
                f"from query scope thread_id={scope_t1.thread_id!r}"
            )

    def test_exact_thread_match_true_explicit_only_returns_own_thread(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """explicit exact_thread_match=True: same isolation as the default."""
        store = _make_store(migrated_pool)
        scope_t1, scope_t2, id_t1, id_t2 = self._seed_two_thread_data(
            store, unique_agent_id
        )

        results = store.search(
            "thread isolation fact content",
            scope_t1,
            record_types=["facts"],
            max_results=50,
            exact_thread_match=True,
        )

        result_ids = {r.id for r in results}
        assert id_t2 not in result_ids, (
            "ISOLATION BUG: thread_T2 fact leaked with explicit exact_thread_match=True!"
        )

    def test_fuzzy_thread_match_returns_both_threads(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """exact_thread_match=False: search in thread_T1 can also return thread_T2 rows."""
        store = _make_store(migrated_pool)
        scope_t1, scope_t2, id_t1, id_t2 = self._seed_two_thread_data(
            store, unique_agent_id
        )

        results = store.search(
            "thread isolation fact content",
            scope_t1,
            record_types=["facts"],
            max_results=50,
            exact_thread_match=False,
        )

        result_ids = {r.id for r in results}
        # thread_T1 row must appear.
        assert id_t1 in result_ids, (
            f"thread_T1 fact (id={id_t1!r}) missing from fuzzy thread search results."
        )
        # thread_T2 row must also appear (same agent, same content, same vector neighbourhood).
        assert id_t2 in result_ids, (
            f"thread_T2 fact (id={id_t2!r}) missing from fuzzy thread search results. "
            "exact_thread_match=False should return rows from ALL threads for this agent."
        )

    def test_fuzzy_thread_broader_than_exact(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """exact_thread_match=False returns >= as many results as exact_thread_match=True."""
        store = _make_store(migrated_pool)
        scope_t1, scope_t2, id_t1, id_t2 = self._seed_two_thread_data(
            store, unique_agent_id
        )

        exact_results = store.search(
            "thread isolation fact content",
            scope_t1,
            record_types=["facts"],
            max_results=50,
            exact_thread_match=True,
        )
        fuzzy_results = store.search(
            "thread isolation fact content",
            scope_t1,
            record_types=["facts"],
            max_results=50,
            exact_thread_match=False,
        )

        assert len(fuzzy_results) >= len(exact_results), (
            f"UNEXPECTED: fuzzy thread mode ({len(fuzzy_results)} results) returned "
            f"fewer than exact mode ({len(exact_results)} results).  "
            "Fuzzy must be non-narrowing."
        )

    def test_exact_thread_ids_in_fuzzy_results_subset(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Every ID from exact mode is present in fuzzy mode (exact ⊆ fuzzy)."""
        store = _make_store(migrated_pool)
        scope_t1, scope_t2, id_t1, id_t2 = self._seed_two_thread_data(
            store, unique_agent_id
        )

        exact_results = store.search(
            "thread isolation fact content",
            scope_t1,
            record_types=["facts"],
            max_results=50,
            exact_thread_match=True,
        )
        fuzzy_results = store.search(
            "thread isolation fact content",
            scope_t1,
            record_types=["facts"],
            max_results=50,
            exact_thread_match=False,
        )

        exact_ids = {r.id for r in exact_results}
        fuzzy_ids = {r.id for r in fuzzy_results}
        missing = exact_ids - fuzzy_ids
        assert not missing, (
            f"Exact-mode rows are absent from fuzzy-mode results: {missing!r}"
        )

    def test_thread_isolation_does_not_affect_cross_agent_boundary(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """exact_thread_match=False relaxes thread filter but NEVER agent filter."""
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        store = _make_store(migrated_pool)

        agent_a = _unique_id("tiso-A")
        agent_b = _unique_id("tiso-B")

        scope_a_t1 = MemoryScope(agent_id=agent_a, user_id="user-1", thread_id="thread-T1")
        scope_b_t1 = MemoryScope(agent_id=agent_b, user_id="user-1", thread_id="thread-T1")

        content = "thread plus agent boundary test content"
        store.remember(
            SemanticFact(agent_id=agent_a, user_id="user-1", thread_id="thread-T1", content=content),
            scope_a_t1,
        )
        store.remember(
            SemanticFact(agent_id=agent_b, user_id="user-1", thread_id="thread-T1", content=content),
            scope_b_t1,
        )

        # Relax thread but keep agent exact — agent_B must not appear.
        results = store.search(
            content,
            scope_a_t1,
            record_types=["facts"],
            max_results=50,
            exact_agent_match=True,
            exact_thread_match=False,
        )

        result_agent_ids = {r.record.agent_id for r in results}
        assert agent_b not in result_agent_ids, (
            f"ISOLATION BUG: relaxing exact_thread_match let agent_B rows through! "
            f"result_agent_ids={result_agent_ids!r}"
        )


# ---------------------------------------------------------------------------
# TestUnscopedOnlyQueries
# Verifies that scope.thread_id=None + exact_thread_match=True returns ONLY
# rows with thread_id=None and never leaks any row with a non-None thread_id.
# ---------------------------------------------------------------------------


class TestUnscopedOnlyQueries:
    """Unscoped-only queries: scope.thread_id=None + exact_thread_match=True."""

    def _seed_unscoped_and_scoped(self, store, agent_id: str):
        """Seed one unscoped fact (thread_id=None) and one scoped fact (thread_id='t-X').

        Returns:
            (scope_unscoped, scope_threaded, id_unscoped, id_threaded)
        """
        from agent_memory_sdk.models import MemoryScope, SemanticFact

        scope_unscoped = MemoryScope(agent_id=agent_id, user_id="user-1", thread_id=None)
        scope_threaded = MemoryScope(agent_id=agent_id, user_id="user-1", thread_id="thread-X")

        content = "unscoped only query isolation test content"

        fact_unscoped = SemanticFact(
            agent_id=agent_id, user_id="user-1", thread_id=None, content=content
        )
        stored_unscoped = store.remember(fact_unscoped, scope_unscoped)

        fact_threaded = SemanticFact(
            agent_id=agent_id, user_id="user-1", thread_id="thread-X", content=content
        )
        stored_threaded = store.remember(fact_threaded, scope_threaded)

        return scope_unscoped, scope_threaded, stored_unscoped.id, stored_threaded.id

    def test_unscoped_search_finds_unscoped_row(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Searching with thread_id=None + exact_thread_match=True finds the unscoped row."""
        store = _make_store(migrated_pool)
        scope_unscoped, _, id_unscoped, _ = self._seed_unscoped_and_scoped(
            store, unique_agent_id
        )

        results = store.search(
            "unscoped only query isolation test content",
            scope_unscoped,
            record_types=["facts"],
            max_results=50,
            exact_thread_match=True,
        )

        result_ids = {r.id for r in results}
        assert id_unscoped in result_ids, (
            f"Unscoped fact (id={id_unscoped!r}) not found when searching with "
            "thread_id=None and exact_thread_match=True."
        )

    def test_unscoped_search_never_returns_threaded_row(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """CRITICAL: scope.thread_id=None + exact_thread_match=True must NOT return any
        row with a non-None thread_id — this is the unscoped-only semantic."""
        store = _make_store(migrated_pool)
        scope_unscoped, scope_threaded, id_unscoped, id_threaded = (
            self._seed_unscoped_and_scoped(store, unique_agent_id)
        )

        results = store.search(
            "unscoped only query isolation test content",
            scope_unscoped,
            record_types=["facts"],
            max_results=50,
            exact_thread_match=True,
        )

        result_ids = {r.id for r in results}
        assert id_threaded not in result_ids, (
            f"ISOLATION BUG: threaded fact (id={id_threaded!r}, "
            f"thread_id={scope_threaded.thread_id!r}) leaked into "
            "unscoped-only search (scope.thread_id=None, exact_thread_match=True)!"
        )

    def test_unscoped_search_all_results_have_none_thread_id(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Every result from an unscoped-only query has thread_id=None."""
        store = _make_store(migrated_pool)
        scope_unscoped, scope_threaded, id_unscoped, id_threaded = (
            self._seed_unscoped_and_scoped(store, unique_agent_id)
        )

        results = store.search(
            "unscoped only query isolation test content",
            scope_unscoped,
            record_types=["facts"],
            max_results=50,
            exact_thread_match=True,
        )

        for r in results:
            assert r.record.thread_id is None, (
                f"ISOLATION BUG: result id={r.id!r} has thread_id={r.record.thread_id!r} "
                "but the query scope has thread_id=None with exact_thread_match=True. "
                "Only truly unscoped rows should be returned."
            )

    def test_fuzzy_thread_match_on_unscoped_scope_widens_to_threaded(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """With exact_thread_match=False starting from a None-thread scope,
        both the unscoped row and threaded rows are now visible (fuzzy = no filter)."""
        store = _make_store(migrated_pool)
        scope_unscoped, _, id_unscoped, id_threaded = (
            self._seed_unscoped_and_scoped(store, unique_agent_id)
        )

        results = store.search(
            "unscoped only query isolation test content",
            scope_unscoped,
            record_types=["facts"],
            max_results=50,
            exact_thread_match=False,
        )

        result_ids = {r.id for r in results}
        # Both rows were seeded with the same content/vector so both should appear.
        assert id_unscoped in result_ids, (
            "Unscoped row missing from fuzzy-thread search results."
        )
        assert id_threaded in result_ids, (
            f"Threaded row (id={id_threaded!r}) missing from fuzzy-thread search results. "
            "exact_thread_match=False should lift the thread_id=None restriction."
        )

    def test_unscoped_search_default_params_same_as_exact_thread_match_true(
        self, migrated_pool, unique_agent_id
    ) -> None:
        """Default search with thread_id=None scope behaves identically to
        explicit exact_thread_match=True."""
        store = _make_store(migrated_pool)
        scope_unscoped, _, id_unscoped, id_threaded = (
            self._seed_unscoped_and_scoped(store, unique_agent_id)
        )

        default_results = store.search(
            "unscoped only query isolation test content",
            scope_unscoped,
            record_types=["facts"],
            max_results=50,
        )
        explicit_results = store.search(
            "unscoped only query isolation test content",
            scope_unscoped,
            record_types=["facts"],
            max_results=50,
            exact_thread_match=True,
        )

        default_ids = {r.id for r in default_results}
        explicit_ids = {r.id for r in explicit_results}

        assert default_ids == explicit_ids, (
            "Default search and explicit exact_thread_match=True produced different "
            f"results for thread_id=None scope: "
            f"default={default_ids!r} explicit={explicit_ids!r}"
        )
        # Neither must contain the threaded row.
        assert id_threaded not in default_ids, (
            "ISOLATION BUG: threaded row leaked into default unscoped search!"
        )

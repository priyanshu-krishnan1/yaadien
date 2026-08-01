"""
tests/test_pipe2_ingest_resolver.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for PIPE-2: pluggable ADD/UPDATE/DELETE/NOOP ingest resolver at
write time.

Coverage:
  - IngestAction enum values
  - IngestDecision dataclass (fields, defaults, equality)
  - NoOpIngestResolver default behaviour (always ADD)
  - IngestResolver protocol structural check
  - store._cosine_distance() helper (identical/orthogonal/opposite vectors,
    empty/zero/mismatched-length edge cases)
  - MemoryStore default path: NoOpIngestResolver skips the similarity
    search entirely (byte-for-byte pre-PIPE-2 behaviour)
  - MemoryStore.remember() wired to a configured IngestResolver:
      * ADD decision — inserts as today
      * UPDATE decision — merges candidate into target via update()
      * DELETE decision — forgets the target, candidate not written
      * NOOP decision — nothing written
      * fallbacks: UPDATE/DELETE with missing or unresolvable target_id
      * resolver exception -> falls back to ADD
      * search() exception -> resolver still called with similar=[]
  - resolver_k is forwarded as search()'s top_k
  - consolidator only fires for ADD decisions on working/episodic writes
  - resolver_k / consolidate_every_n constructor validation

No live Db2 instance required — uses the same queued-fake-pool pattern as
tests/test_pipe1_hybrid.py.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_memory_sdk.models import MemoryScope, SemanticFact, WorkingMemory
from agent_memory_sdk.repositories.facts import SemanticFactRepository
from agent_memory_sdk.repositories.working import WorkingMemoryRepository
from agent_memory_sdk.store import MemoryStore, _cosine_distance
from agent_memory_sdk.types import (
    IngestAction,
    IngestDecision,
    IngestResolver,
    NoOpIngestResolver,
)

# ---------------------------------------------------------------------------
# Fake connection pool — queued per-call rows (same pattern as
# tests/test_pipe1_hybrid.py), needed because a single remember() call with
# a resolver configured can issue several sequential SQL statements
# (search step1, search step2, get_by_id, update/forget/create...).
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, call_returns: list[list[tuple[Any, ...]]] | None = None) -> None:
        self._queue: list[list[tuple[Any, ...]]] = list(call_returns or [])
        self._current: list[tuple[Any, ...]] = []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount: int = 0
        self.all_sqls: list[str] = []
        self.all_params: list[list[Any]] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> None:
        self.last_sql = sql
        self.last_params = list(params) if params else []
        self.all_sqls.append(self.last_sql)
        self.all_params.append(self.last_params)
        if self._queue:
            self._current = self._queue.pop(0)
        else:
            self._current = []
        self.rowcount = len(self._current)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._current[0] if self._current else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._current)


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
    def __init__(self, call_returns: list[list[tuple[Any, ...]]] | None = None) -> None:
        self.cursor = _FakeCursor(call_returns)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):  # type: ignore[return]
        yield self.conn


# ---------------------------------------------------------------------------
# Shared constants / row builders
# ---------------------------------------------------------------------------

_SCOPE = MemoryScope(agent_id="agent-001", tenant_id="t1")
_NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
_VEC = [0.1] * 1536
_VEC_STR = "[" + ",".join("0.1" for _ in range(1536)) + "]"


def _fact_row(
    id_: str = "fact-existing",
    content: str = "User prefers dark mode.",
    version: int = 1,
    embedding_str: str = _VEC_STR,
) -> tuple[Any, ...]:
    """Build a fake DB row matching SemanticFactRepository._SELECT_COLS (18 cols)."""
    return (
        id_, "t1", "agent-001", None, None,
        content, json.dumps({}),
        embedding_str,
        1.0,
        "hash",
        _NOW, _NOW, None, version, None,
        None, None, None,   # superseded_by, superseded_at, supersede_reason
    )


def _wm_row(id_: str = "wm-1", content: str = "hello") -> tuple[Any, ...]:
    """Build a fake WorkingMemory row (16 cols: 15 base + consolidated_at)."""
    return (
        id_, None, "agent-001", None, None,
        content, "{}",
        _VEC_STR,
        1.0, "hash",
        _NOW, _NOW, None, 1, None,
        None,  # consolidated_at
    )


# ---------------------------------------------------------------------------
# IngestAction
# ---------------------------------------------------------------------------


class TestIngestAction:
    def test_values(self) -> None:
        assert IngestAction.ADD == "ADD"
        assert IngestAction.UPDATE == "UPDATE"
        assert IngestAction.DELETE == "DELETE"
        assert IngestAction.NOOP == "NOOP"

    def test_is_str_enum(self) -> None:
        assert isinstance(IngestAction.ADD, str)


# ---------------------------------------------------------------------------
# IngestDecision
# ---------------------------------------------------------------------------


class TestIngestDecision:
    def test_add_needs_no_target(self) -> None:
        d = IngestDecision(action=IngestAction.ADD)
        assert d.action == IngestAction.ADD
        assert d.target_id is None
        assert d.reason == ""

    def test_update_with_target_and_reason(self) -> None:
        d = IngestDecision(action=IngestAction.UPDATE, target_id="f1", reason="refines")
        assert d.target_id == "f1"
        assert d.reason == "refines"

    def test_equality(self) -> None:
        a = IngestDecision(action=IngestAction.DELETE, target_id="x", reason="r")
        b = IngestDecision(action=IngestAction.DELETE, target_id="x", reason="r")
        assert a == b

    def test_inequality_on_action(self) -> None:
        a = IngestDecision(action=IngestAction.NOOP)
        b = IngestDecision(action=IngestAction.ADD)
        assert a != b


# ---------------------------------------------------------------------------
# NoOpIngestResolver
# ---------------------------------------------------------------------------


class TestNoOpIngestResolver:
    def test_always_returns_add(self) -> None:
        noop = NoOpIngestResolver()
        fact = SemanticFact(agent_id="agent-001", content="x")
        result = noop(fact, [])
        assert result.action == IngestAction.ADD
        assert result.target_id is None

    def test_returns_add_even_with_similar_records(self) -> None:
        noop = NoOpIngestResolver()
        fact = SemanticFact(agent_id="agent-001", content="x")
        existing = SemanticFact(agent_id="agent-001", content="y")
        result = noop(fact, [(existing, 0.01)])
        assert result.action == IngestAction.ADD

    def test_is_callable(self) -> None:
        assert callable(NoOpIngestResolver())


# ---------------------------------------------------------------------------
# IngestResolver protocol structural check
# ---------------------------------------------------------------------------


class TestIngestResolverProtocol:
    def test_noop_satisfies_protocol_shape(self) -> None:
        noop = NoOpIngestResolver()
        fact = SemanticFact(agent_id="agent-001", content="x")
        result = noop(fact, [])
        assert isinstance(result, IngestDecision)

    def test_custom_resolver_callable(self) -> None:
        class _CustomResolver:
            def __call__(self, candidate: Any, similar: list) -> IngestDecision:
                return IngestDecision(action=IngestAction.NOOP)

        resolver: IngestResolver = _CustomResolver()
        fact = SemanticFact(agent_id="agent-001", content="x")
        assert resolver(fact, []).action == IngestAction.NOOP


# ---------------------------------------------------------------------------
# _cosine_distance
# ---------------------------------------------------------------------------


class TestCosineDistance:
    def test_identical_vectors_zero_distance(self) -> None:
        assert _cosine_distance([1.0, 0.0], [1.0, 0.0]) == pytest.approx(0.0, abs=1e-9)

    def test_orthogonal_vectors_distance_one(self) -> None:
        assert _cosine_distance([1.0, 0.0], [0.0, 1.0]) == pytest.approx(1.0, abs=1e-9)

    def test_opposite_vectors_distance_two(self) -> None:
        assert _cosine_distance([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(2.0, abs=1e-9)

    def test_empty_a_returns_max_distance(self) -> None:
        assert _cosine_distance([], [1.0, 0.0]) == 1.0

    def test_empty_b_returns_max_distance(self) -> None:
        assert _cosine_distance([1.0, 0.0], []) == 1.0

    def test_mismatched_length_returns_max_distance(self) -> None:
        assert _cosine_distance([1.0, 0.0], [1.0, 0.0, 0.0]) == 1.0

    def test_zero_vector_returns_max_distance(self) -> None:
        assert _cosine_distance([0.0, 0.0], [1.0, 0.0]) == 1.0

    def test_similar_but_not_identical_vectors(self) -> None:
        d = _cosine_distance([1.0, 0.0], [1.0, 0.01])
        assert 0.0 < d < 0.01


# ---------------------------------------------------------------------------
# MemoryStore default path (NoOpIngestResolver) — unchanged behavior
# ---------------------------------------------------------------------------


class TestDefaultPathUnchanged:
    def test_no_resolver_configured_uses_noop_ingest_resolver(self) -> None:
        pool = _FakePool([[]])
        store = MemoryStore(pool)
        assert isinstance(store._ingest_resolver, NoOpIngestResolver)

    def test_default_path_skips_similarity_search(self) -> None:
        """With the default resolver, remember() must not issue a search()
        query at all — only the dedup SELECT + INSERT from create()."""
        pool = _FakePool([[]])  # dedup SELECT returns no existing row
        store = MemoryStore(pool)
        fact = SemanticFact(agent_id="agent-001", content="new fact")
        store.remember(fact, _SCOPE)
        # No VECTOR_DISTANCE-ordered SQL should have been issued.
        assert not any("VECTOR_DISTANCE" in sql for sql in pool.cursor.all_sqls)
        # Only the dedup SELECT + INSERT (2 calls).
        assert len(pool.cursor.all_sqls) == 2

    def test_default_path_returns_created_record(self) -> None:
        pool = _FakePool([[]])
        store = MemoryStore(pool)
        fact = SemanticFact(agent_id="agent-001", content="new fact")
        result = store.remember(fact, _SCOPE)
        assert result.id == fact.id


# ---------------------------------------------------------------------------
# MemoryStore + configured IngestResolver — ADD decision
# ---------------------------------------------------------------------------


class _FixedResolver:
    """Test double: returns a pre-baked IngestDecision regardless of input."""

    def __init__(self, decision: IngestDecision) -> None:
        self._decision = decision
        self.calls: list[tuple[Any, list]] = []

    def __call__(self, candidate: Any, similar: list) -> IngestDecision:
        self.calls.append((candidate, similar))
        return self._decision


class TestResolverAddDecision:
    def test_add_decision_runs_search_then_creates(self) -> None:
        # search() step1 (ids) -> step2 (rows) -> create(): dedup select -> insert
        pool = _FakePool([
            [("fact-existing",)],   # search step1: ordered ids
            [_fact_row()],          # search step2: full rows
            [],                     # create() dedup select: no existing dup
        ])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.ADD))
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="new fact", embedding=_VEC)

        result = store.remember(fact, _SCOPE)

        assert result.id == fact.id
        # 2 search calls + 1 dedup select + 1 insert = 4
        assert len(pool.cursor.all_sqls) == 4
        assert any("VECTOR_DISTANCE" in sql for sql in pool.cursor.all_sqls)
        assert any("INSERT INTO semantic_facts" in sql for sql in pool.cursor.all_sqls)

    def test_resolver_receives_similar_records_with_distances(self) -> None:
        pool = _FakePool([
            [("fact-existing",)],
            [_fact_row()],
            [],
        ])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.ADD))
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="new fact", embedding=_VEC)

        store.remember(fact, _SCOPE)

        assert len(resolver.calls) == 1
        candidate, similar = resolver.calls[0]
        assert candidate is fact
        assert len(similar) == 1
        existing_record, distance = similar[0]
        assert existing_record.id == "fact-existing"
        assert isinstance(distance, float)
        # Candidate and existing row share the same constant vector -> ~0 distance.
        assert distance == pytest.approx(0.0, abs=1e-6)

    def test_resolver_k_forwarded_as_top_k(self) -> None:
        pool = _FakePool([
            [("fact-existing",)],
            [_fact_row()],
            [],
        ])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.ADD))
        store = MemoryStore(pool, ingest_resolver=resolver, resolver_k=3)
        fact = SemanticFact(agent_id="agent-001", content="new fact", embedding=_VEC)

        store.remember(fact, _SCOPE)

        # Step-1 search SQL's id_params tuple ends with fetch_k == top_k (3).
        first_params = pool.cursor.all_params[0]
        assert first_params[-1] == 3

    def test_no_similar_records_found_calls_resolver_with_empty_list(self) -> None:
        pool = _FakePool([
            [],   # search step1: no ids found
            [],   # create() dedup select
        ])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.ADD))
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="new fact", embedding=_VEC)

        store.remember(fact, _SCOPE)

        assert resolver.calls[0][1] == []


# ---------------------------------------------------------------------------
# MemoryStore + configured IngestResolver — UPDATE decision
# ---------------------------------------------------------------------------


class TestResolverUpdateDecision:
    def test_update_merges_candidate_into_target_no_insert(self) -> None:
        pool = _FakePool([
            [("fact-existing",)],       # search step1
            [_fact_row()],              # search step2
            [_fact_row()],              # get_by_id(target_id) -> found
            [("ok",)],                  # update() UPDATE statement; len>0 -> rowcount=1 (lock check passes)
        ])
        resolver = _FixedResolver(
            IngestDecision(action=IngestAction.UPDATE, target_id="fact-existing", reason="refines")
        )
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="updated content", embedding=_VEC)

        result = store.remember(fact, _SCOPE)

        assert result.id == "fact-existing"
        assert result.content == "updated content"
        assert not any("INSERT INTO semantic_facts" in sql for sql in pool.cursor.all_sqls)
        assert any("UPDATE semantic_facts" in sql for sql in pool.cursor.all_sqls)

    def test_update_missing_target_id_falls_back_to_add(self) -> None:
        pool = _FakePool([
            [("fact-existing",)],
            [_fact_row()],
            [],   # create() dedup select
        ])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.UPDATE, target_id=None))
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="new fact", embedding=_VEC)

        result = store.remember(fact, _SCOPE)

        assert result.id == fact.id
        assert any("INSERT INTO semantic_facts" in sql for sql in pool.cursor.all_sqls)

    def test_update_unresolvable_target_id_falls_back_to_add(self) -> None:
        pool = _FakePool([
            [("fact-existing",)],
            [_fact_row()],
            [],   # get_by_id(target_id) -> not found
            [],   # create() dedup select
        ])
        resolver = _FixedResolver(
            IngestDecision(action=IngestAction.UPDATE, target_id="ghost-id")
        )
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="new fact", embedding=_VEC)

        result = store.remember(fact, _SCOPE)

        assert result.id == fact.id
        assert any("INSERT INTO semantic_facts" in sql for sql in pool.cursor.all_sqls)

    def test_update_logs_warning_on_missing_target(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        pool = _FakePool([
            [("fact-existing",)],
            [_fact_row()],
            [],
            [],
        ])
        resolver = _FixedResolver(
            IngestDecision(action=IngestAction.UPDATE, target_id="ghost-id")
        )
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="new fact", embedding=_VEC)

        with caplog.at_level(logging.WARNING, logger="agent_memory_sdk.store"):
            store.remember(fact, _SCOPE)

        warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("ghost-id" in m for m in warn_msgs)


# ---------------------------------------------------------------------------
# MemoryStore + configured IngestResolver — DELETE decision
# ---------------------------------------------------------------------------


class TestResolverDeleteDecision:
    def test_delete_forgets_target_candidate_not_written(self) -> None:
        pool = _FakePool([
            [("fact-existing",)],   # search step1
            [_fact_row()],          # search step2
            [("ok",)],              # forget() UPDATE statement; len>0 -> rowcount=1 (success)
        ])
        resolver = _FixedResolver(
            IngestDecision(action=IngestAction.DELETE, target_id="fact-existing", reason="contradicted")
        )
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="obsolete info", embedding=_VEC)

        result = store.remember(fact, _SCOPE)

        # Candidate is returned as-is (never persisted).
        assert result is fact
        assert not any("INSERT INTO semantic_facts" in sql for sql in pool.cursor.all_sqls)
        last_sql = pool.cursor.last_sql
        assert "UPDATE semantic_facts" in last_sql
        assert "deleted_at = ?" in last_sql

    def test_delete_missing_target_id_logs_warning_and_writes_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        pool = _FakePool([
            [("fact-existing",)],
            [_fact_row()],
        ])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.DELETE, target_id=None))
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="x", embedding=_VEC)

        with caplog.at_level(logging.WARNING, logger="agent_memory_sdk.store"):
            result = store.remember(fact, _SCOPE)

        assert result is fact
        assert not any("INSERT INTO semantic_facts" in sql for sql in pool.cursor.all_sqls)
        assert not any("UPDATE semantic_facts" in sql for sql in pool.cursor.all_sqls)
        warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("DELETE" in m for m in warn_msgs)


# ---------------------------------------------------------------------------
# MemoryStore + configured IngestResolver — NOOP decision
# ---------------------------------------------------------------------------


class TestResolverNoopDecision:
    def test_noop_writes_nothing(self) -> None:
        pool = _FakePool([
            [("fact-existing",)],
            [_fact_row()],
        ])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.NOOP, reason="duplicate"))
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="duplicate fact", embedding=_VEC)

        result = store.remember(fact, _SCOPE)

        assert result is fact
        assert len(pool.cursor.all_sqls) == 2  # only the two search() calls
        assert not any("INSERT" in sql or "UPDATE" in sql for sql in pool.cursor.all_sqls)


# ---------------------------------------------------------------------------
# Resolver / search exception handling
# ---------------------------------------------------------------------------


class TestResolverExceptionHandling:
    def test_resolver_exception_falls_back_to_add(self) -> None:
        pool = _FakePool([
            [("fact-existing",)],
            [_fact_row()],
            [],   # create() dedup select
        ])

        class _BrokenResolver:
            def __call__(self, candidate: Any, similar: list) -> IngestDecision:
                raise RuntimeError("LLM call failed")

        store = MemoryStore(pool, ingest_resolver=_BrokenResolver())
        fact = SemanticFact(agent_id="agent-001", content="new fact", embedding=_VEC)

        result = store.remember(fact, _SCOPE)

        assert result.id == fact.id
        assert any("INSERT INTO semantic_facts" in sql for sql in pool.cursor.all_sqls)

    def test_search_exception_still_calls_resolver_with_empty_similar(self) -> None:
        # Exercise the guard by monkeypatching repo.search() to raise,
        # simulating a Db2 failure mid-query — remember() must recover by
        # calling the resolver with an empty similar-records list rather
        # than propagating the exception.
        pool = _FakePool([[]])

        calls: list[list] = []

        class _RecordingResolver:
            def __call__(self, candidate: Any, similar: list) -> IngestDecision:
                calls.append(similar)
                return IngestDecision(action=IngestAction.NOOP)

        store = MemoryStore(pool, ingest_resolver=_RecordingResolver())

        # Monkeypatch repo.search to raise, simulating a Db2 failure mid-query.
        def _raise_search(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Db2 connection lost")

        store.facts.search = _raise_search  # type: ignore[method-assign]
        fact = SemanticFact(agent_id="agent-001", content="new fact", embedding=_VEC)

        result = store.remember(fact, _SCOPE)

        assert result is fact  # NOOP path
        assert calls == [[]]


# ---------------------------------------------------------------------------
# Candidate embedding resolution
# ---------------------------------------------------------------------------


class TestCandidateEmbeddingResolution:
    def test_uses_candidate_embedding_when_already_set(self) -> None:
        pool = _FakePool([
            [("fact-existing",)],
            [_fact_row()],
        ])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.NOOP))
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="x", embedding=_VEC)

        store.remember(fact, _SCOPE)

        # search() was actually invoked (2 SQL calls) using the pre-set embedding.
        assert len(pool.cursor.all_sqls) == 2

    def test_no_embedding_and_no_provider_skips_search(self) -> None:
        pool = _FakePool([[]])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.NOOP))
        store = MemoryStore(pool, ingest_resolver=resolver)
        fact = SemanticFact(agent_id="agent-001", content="x")  # no embedding set

        store.remember(fact, _SCOPE)

        assert pool.cursor.all_sqls == []  # search skipped entirely
        assert resolver.calls[0][1] == []

    def test_embedding_provider_used_when_candidate_has_no_embedding(self) -> None:
        pool = _FakePool([
            [("fact-existing",)],
            [_fact_row()],
        ])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.NOOP))

        def _provider(text: str) -> list[float]:
            return _VEC

        # enable_chunking=False keeps repo._chunk_repo None so search() uses
        # the standard 2-step path (not the ORC-2 chunk-search path) even
        # though an embedding_provider is configured.
        store = MemoryStore(
            pool, ingest_resolver=resolver, embedding_provider=_provider, enable_chunking=False
        )
        fact = SemanticFact(agent_id="agent-001", content="x")  # no embedding set

        store.remember(fact, _SCOPE)

        assert len(pool.cursor.all_sqls) == 2  # search was invoked via the provider

    def test_embedding_provider_exception_skips_search(self) -> None:
        pool = _FakePool([[]])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.NOOP))

        def _broken_provider(text: str) -> list[float]:
            raise RuntimeError("embedding service down")

        store = MemoryStore(
            pool,
            ingest_resolver=resolver,
            embedding_provider=_broken_provider,
            enable_chunking=False,
        )
        fact = SemanticFact(agent_id="agent-001", content="x")

        store.remember(fact, _SCOPE)

        assert pool.cursor.all_sqls == []
        assert resolver.calls[0][1] == []


# ---------------------------------------------------------------------------
# Interaction with the Consolidator: only ADD decisions trigger it
# ---------------------------------------------------------------------------


class TestConsolidatorInteraction:
    def test_add_decision_on_working_memory_runs_consolidator(self) -> None:
        pool = _FakePool([
            [("wm-existing",)],   # search step1
            [_wm_row(id_="wm-existing")],  # search step2
            # create(): WorkingMemoryRepository has _DEDUP_ON_WRITE=False -> no dedup select
        ])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.ADD))

        derived_calls: list[list[Any]] = []

        class _RecordingConsolidator:
            def __call__(self, raw_memories: list[Any]) -> list[Any]:
                derived_calls.append(raw_memories)
                return []

        store = MemoryStore(
            pool, ingest_resolver=resolver, consolidator=_RecordingConsolidator()
        )
        wm = WorkingMemory(agent_id="agent-001", content="hi", embedding=_VEC)

        store.remember(wm, _SCOPE)

        assert len(derived_calls) == 1

    def test_noop_decision_on_working_memory_skips_consolidator(self) -> None:
        pool = _FakePool([
            [("wm-existing",)],
            [_wm_row(id_="wm-existing")],
        ])
        resolver = _FixedResolver(IngestDecision(action=IngestAction.NOOP))

        derived_calls: list[list[Any]] = []

        class _RecordingConsolidator:
            def __call__(self, raw_memories: list[Any]) -> list[Any]:
                derived_calls.append(raw_memories)
                return []

        store = MemoryStore(
            pool, ingest_resolver=resolver, consolidator=_RecordingConsolidator()
        )
        wm = WorkingMemory(agent_id="agent-001", content="hi", embedding=_VEC)

        store.remember(wm, _SCOPE)

        assert derived_calls == []

    def test_update_decision_skips_consolidator(self) -> None:
        pool = _FakePool([
            [("wm-existing",)],
            [_wm_row(id_="wm-existing")],
            [_wm_row(id_="wm-existing")],  # get_by_id
            [("ok",)],                     # update(); len>0 -> rowcount=1 (lock check passes)
        ])
        resolver = _FixedResolver(
            IngestDecision(action=IngestAction.UPDATE, target_id="wm-existing")
        )

        derived_calls: list[list[Any]] = []

        class _RecordingConsolidator:
            def __call__(self, raw_memories: list[Any]) -> list[Any]:
                derived_calls.append(raw_memories)
                return []

        store = MemoryStore(
            pool, ingest_resolver=resolver, consolidator=_RecordingConsolidator()
        )
        wm = WorkingMemory(agent_id="agent-001", content="hi", embedding=_VEC)

        store.remember(wm, _SCOPE)

        assert derived_calls == []


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


class TestConstructorValidation:
    def test_resolver_k_must_be_positive(self) -> None:
        pool = _FakePool([[]])
        with pytest.raises(ValueError, match="resolver_k"):
            MemoryStore(pool, resolver_k=0)

    def test_resolver_k_default_is_five(self) -> None:
        pool = _FakePool([[]])
        store = MemoryStore(pool)
        assert store._resolver_k == 5

    def test_resolver_k_custom_value_stored(self) -> None:
        pool = _FakePool([[]])
        store = MemoryStore(pool, resolver_k=10)
        assert store._resolver_k == 10


# ---------------------------------------------------------------------------
# Repositories are unaffected by import (sanity import check)
# ---------------------------------------------------------------------------


class TestSanityImports:
    def test_repo_types_importable(self) -> None:
        assert SemanticFactRepository is not None
        assert WorkingMemoryRepository is not None

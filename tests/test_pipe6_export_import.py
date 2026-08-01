"""
tests/test_pipe6_export_import.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for PIPE-6: MemoryStore.export_scope() / MemoryStore.import_scope()
and the ChunkRepository.list_all() helper they rely on for memory_chunks.

Coverage:
  1. export_scope() tags every yielded record with the correct "_type"
     discriminator across all five memory tables plus memory_chunks.
  2. export_scope() produces JSON-serializable output (datetimes as ISO
     strings, embedding left as a raw list[float]).
  3. export_scope() omits memory_chunks entirely when chunking isn't wired in.
  4. export_scope() paginates internally in batches (no single unbounded
     fetch for a large scope).
  5. import_scope() re-inserts records via the correct repo's create() /
     ChunkRepository.insert_chunk(), returns per-table counts.
  6. import_scope() rejects records with a missing/unknown "_type".
  7. import_scope() rejects a scope mismatch with ScopeMismatchError
     *before* issuing any create()/insert_chunk() call — the critical
     "reject, don't silently rewrite" safety property.
  8. Round-trip fidelity: model_dump(mode="json") + "_type" tag -> import_scope()
     reconstructs an equivalent record via create().

No live Db2 instance required — uses the same fake-pool pattern as
test_lifecycle.py / test_orc2.py.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from agent_memory_sdk import store as store_module
from agent_memory_sdk.exceptions import ScopeMismatchError
from agent_memory_sdk.models import (
    EntityProfile,
    EpisodicMemory,
    MemoryScope,
    ProceduralMemory,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.repositories.chunks import ChunkRepository
from agent_memory_sdk.store import MemoryStore

# ---------------------------------------------------------------------------
# Fake connection pool (same pattern as test_lifecycle.py / test_orc2.py)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount = len(self.rows)
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
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor = _FakeCursor(rows)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):
        yield self.conn


_NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
_SCOPE = MemoryScope(agent_id="agent-001", tenant_id="t1")
_VEC = [0.1] * 1536


def _hash(content: str) -> str:
    import hashlib
    import re

    return hashlib.sha256(re.sub(r"\s+", " ", content.lower()).strip().encode()).hexdigest()


def _vec_str() -> str:
    return "[" + ",".join("0.1" for _ in range(1536)) + "]"


def _base_row(id_: str, content: str) -> tuple[Any, ...]:
    """15-column row shape (entity_profiles / procedural_memory)."""
    return (
        id_, "t1", "agent-001", None, None,
        content, json.dumps({"k": "v"}),
        _vec_str(),
        1.0, _hash(content),
        _NOW, _NOW, None, 1, None,
    )


def _consolidated_row(id_: str, content: str) -> tuple[Any, ...]:
    """16-column row shape (working_memory / episodic_memory) — adds consolidated_at."""
    return (*_base_row(id_, content), None)


def _facts_row(id_: str, content: str) -> tuple[Any, ...]:
    """18-column row shape (semantic_facts) — adds supersession columns."""
    return (*_base_row(id_, content), None, None, None)


def _chunk_row(id_: str, source_id: str) -> tuple[Any, ...]:
    """11-column row shape matching ChunkRepository.list_all()'s select_cols."""
    return (
        id_, "working_memory", source_id, 0, "chunk text here",
        _vec_str(),
        "t1", "agent-001", None, None,
        _NOW,
    )


def _make_multi_repo_store() -> MemoryStore:
    """A MemoryStore whose five repos each have their own isolated _FakePool.

    A single shared fake pool can't be used to test export_scope() across all
    five tables at once — every repo's list_all() would receive the exact
    same preset row tuple, which has the wrong column count for at least some
    of the five differently-shaped _SELECT_COLS (e.g. semantic_facts expects
    18 columns, entity_profiles expects 15). Overriding each repo's _pool
    individually lets each table return rows shaped correctly for its own
    _model_from_row().
    """
    store = MemoryStore(_FakePool())
    store.working._pool = _FakePool([_consolidated_row("wm-1", "working turn")])
    store.episodic._pool = _FakePool([_consolidated_row("ep-1", "episode summary")])
    store.facts._pool = _FakePool([_facts_row("fact-1", "user likes tea")])
    store.profiles._pool = _FakePool([_base_row("prof-1", "profile summary")])
    store.procedures._pool = _FakePool([_base_row("proc-1", "how to debug")])
    return store


# ---------------------------------------------------------------------------
# export_scope()
# ---------------------------------------------------------------------------


class TestExportScope:
    def test_tags_and_yields_all_five_memory_types(self):
        store = _make_multi_repo_store()

        records = list(store.export_scope(_SCOPE))

        by_type = {r["_type"]: r for r in records}
        assert set(by_type) == {
            "working_memory",
            "episodic_memory",
            "semantic_facts",
            "entity_profiles",
            "procedural_memory",
        }
        assert by_type["working_memory"]["id"] == "wm-1"
        assert by_type["episodic_memory"]["id"] == "ep-1"
        assert by_type["semantic_facts"]["id"] == "fact-1"
        assert by_type["entity_profiles"]["id"] == "prof-1"
        assert by_type["procedural_memory"]["id"] == "proc-1"

    def test_no_chunks_when_chunking_not_wired_in(self):
        """Default MemoryStore(pool) has chunks=None — export must not touch it."""
        store = _make_multi_repo_store()
        assert store.chunks is None

        records = list(store.export_scope(_SCOPE))

        assert all(r["_type"] != "memory_chunks" for r in records)

    def test_includes_chunks_when_chunk_repo_attached(self):
        store = _make_multi_repo_store()
        store.chunks = ChunkRepository(
            _FakePool([_chunk_row("chunk-1", "wm-1")])
        )

        records = list(store.export_scope(_SCOPE))

        chunk_records = [r for r in records if r["_type"] == "memory_chunks"]
        assert len(chunk_records) == 1
        chunk = chunk_records[0]
        assert chunk["id"] == "chunk-1"
        assert chunk["source_table"] == "working_memory"
        assert chunk["source_id"] == "wm-1"
        assert chunk["chunk_index"] == 0
        assert chunk["embedding"] == _VEC
        # created_at must be JSON-serializable (ISO string), not a raw datetime.
        assert isinstance(chunk["created_at"], str)
        assert chunk["created_at"] == _NOW.isoformat()

    def test_embedding_is_raw_float_list_not_a_string(self):
        store = _make_multi_repo_store()

        records = list(store.export_scope(_SCOPE))

        for record in records:
            assert isinstance(record["embedding"], list)
            assert all(isinstance(x, float) for x in record["embedding"])

    def test_datetime_fields_are_iso_strings(self):
        store = _make_multi_repo_store()

        records = list(store.export_scope(_SCOPE))

        for record in records:
            assert isinstance(record["created_at"], str)
            # Pydantic's model_dump(mode="json") renders a UTC datetime with a
            # "Z" suffix rather than "+00:00" — normalize before comparing.
            parsed = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
            assert parsed == _NOW

    def test_output_is_json_serializable(self):
        store = _make_multi_repo_store()
        store.chunks = ChunkRepository(_FakePool([_chunk_row("chunk-1", "wm-1")]))

        for record in store.export_scope(_SCOPE):
            json.dumps(record)  # must not raise

    def test_requires_agent_id_raised_lazily_on_iteration(self):
        """export_scope() is a generator — the ValueError only fires once consumed."""
        store = _make_multi_repo_store()
        gen = store.export_scope(MemoryScope(agent_id=""))
        with pytest.raises(ValueError, match="agent_id"):
            next(gen)

    def test_paginates_across_batches(self, monkeypatch):
        """A scope with more rows than one batch must issue > 1 list_all() call."""
        monkeypatch.setattr(store_module, "_EXPORT_BATCH_SIZE", 2)

        class _PagingCursor(_FakeCursor):
            """Slices self.rows according to the limit/offset actually bound."""

            def execute(self, sql, params=None):
                super().execute(sql, params or [])

            def fetchall(self):
                params = self.last_params
                if "ROW_NUMBER" in self.last_sql:
                    offset, upper = params[-2], params[-1]
                    return self.rows[offset:upper]
                limit = params[-1]
                return self.rows[:limit]

        rows = [_consolidated_row(f"wm-{i}", f"turn {i}") for i in range(5)]
        pool = _FakePool()
        pool.cursor = _PagingCursor(rows)
        pool.conn = _FakeConn(pool.cursor)

        store = MemoryStore(_FakePool())
        store.working._pool = pool
        # Starve the other four tables so only working_memory has data.
        store.episodic._pool = _FakePool([])
        store.facts._pool = _FakePool([])
        store.profiles._pool = _FakePool([])
        store.procedures._pool = _FakePool([])

        records = list(store.export_scope(_SCOPE))
        working_records = [r for r in records if r["_type"] == "working_memory"]

        assert [r["id"] for r in working_records] == [f"wm-{i}" for i in range(5)]
        # 5 rows at batch size 2 -> pages of 2, 2, 1 -> 3 list_all() calls,
        # each of which issues exactly one SQL SELECT against working_memory.
        select_calls = [s for s in pool.cursor.all_sqls if "SELECT" in s]
        assert len(select_calls) == 3


# ---------------------------------------------------------------------------
# import_scope()
# ---------------------------------------------------------------------------


class _CaptureSqlPool:
    """Pool that records every executed SQL statement (from test_lifecycle.py)."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor = _FakeCursor(rows)
        self.conn = _FakeConn(self.cursor)

    @contextmanager
    def get_connection(self):
        yield self.conn


def _exported_working_memory(**overrides: Any) -> dict[str, Any]:
    record = WorkingMemory(
        id="wm-import-1",
        agent_id="agent-001",
        tenant_id="t1",
        content="hello from export",
        metadata={"role": "user"},
        embedding=[0.5, 0.6],
    )
    data = record.model_dump(mode="json")
    data["_type"] = "working_memory"
    data.update(overrides)
    return data


def _exported_fact(**overrides: Any) -> dict[str, Any]:
    record = SemanticFact(
        id="fact-import-1",
        agent_id="agent-001",
        tenant_id="t1",
        content="user prefers dark mode",
        confidence=0.9,
    )
    data = record.model_dump(mode="json")
    data["_type"] = "semantic_facts"
    data.update(overrides)
    return data


def _exported_chunk(**overrides: Any) -> dict[str, Any]:
    data = {
        "id": "chunk-import-1",
        "source_table": "working_memory",
        "source_id": "wm-import-1",
        "chunk_index": 0,
        "chunk_text": "a chunk of text",
        "embedding": [0.1, 0.2, 0.3],
        "tenant_id": "t1",
        "agent_id": "agent-001",
        "user_id": None,
        "thread_id": None,
        "created_at": _NOW.isoformat(),
        "_type": "memory_chunks",
    }
    data.update(overrides)
    return data


class TestImportScope:
    def test_creates_working_memory_record(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)

        counts = store.import_scope([_exported_working_memory()], _SCOPE)

        assert counts["working_memory"] == 1
        assert any("INSERT INTO working_memory" in s for s in pool.cursor.all_sqls)

    def test_creates_semantic_fact_record(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)

        counts = store.import_scope([_exported_fact()], _SCOPE)

        assert counts["semantic_facts"] == 1
        assert any("INSERT INTO semantic_facts" in s for s in pool.cursor.all_sqls)

    def test_returns_zero_counts_for_untouched_types(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)

        counts = store.import_scope([_exported_working_memory()], _SCOPE)

        assert counts == {
            "working_memory": 1,
            "episodic_memory": 0,
            "semantic_facts": 0,
            "entity_profiles": 0,
            "procedural_memory": 0,
            "memory_chunks": 0,
        }

    def test_chunk_record_calls_insert_chunk(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        store.chunks = ChunkRepository(pool)

        counts = store.import_scope([_exported_chunk()], _SCOPE)

        assert counts["memory_chunks"] == 1
        assert any("INSERT INTO memory_chunks" in s for s in pool.cursor.all_sqls)

    def test_chunk_record_without_chunk_repo_raises_clear_error(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        assert store.chunks is None

        with pytest.raises(ValueError, match="chunking enabled"):
            store.import_scope([_exported_chunk()], _SCOPE)

    def test_missing_type_field_raises(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        record = _exported_working_memory()
        del record["_type"]

        with pytest.raises(ValueError, match="_type"):
            store.import_scope([record], _SCOPE)

    def test_unknown_type_raises(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        record = _exported_working_memory(_type="not_a_real_table")

        with pytest.raises(ValueError, match="unrecognized"):
            store.import_scope([record], _SCOPE)

    def test_scope_mismatch_raises_scope_mismatch_error(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        record = _exported_working_memory(user_id="someone-else")

        with pytest.raises(ScopeMismatchError):
            store.import_scope([record], _SCOPE)

    def test_scope_mismatch_does_not_call_create(self):
        """The critical safety property: reject before any INSERT is issued."""
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        record = _exported_working_memory(agent_id="some-other-agent")

        with pytest.raises(ScopeMismatchError):
            store.import_scope([record], _SCOPE)

        assert pool.cursor.all_sqls == []

    def test_scope_mismatch_on_tenant_id_is_detected(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        record = _exported_working_memory(tenant_id="different-tenant")

        with pytest.raises(ScopeMismatchError, match="scope mismatch"):
            store.import_scope([record], _SCOPE)

    def test_chunk_scope_mismatch_raises_and_skips_insert(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        store.chunks = ChunkRepository(pool)
        record = _exported_chunk(user_id="someone-else")

        with pytest.raises(ScopeMismatchError):
            store.import_scope([record], _SCOPE)

        assert pool.cursor.all_sqls == []

    def test_matching_scope_with_all_fields_set_succeeds(self):
        """A record whose tenant/user/thread all match a narrower target scope."""
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        narrow_scope = MemoryScope(
            agent_id="agent-001", tenant_id="t1", user_id="user-1", thread_id="thread-1"
        )
        record = _exported_working_memory(
            tenant_id="t1", user_id="user-1", thread_id="thread-1"
        )

        counts = store.import_scope([record], narrow_scope)

        assert counts["working_memory"] == 1

    def test_processes_multiple_records_in_order(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        records = [
            _exported_working_memory(id="wm-a"),
            _exported_working_memory(id="wm-b"),
            _exported_fact(id="fact-a"),
        ]

        counts = store.import_scope(records, _SCOPE)

        assert counts["working_memory"] == 2
        assert counts["semantic_facts"] == 1

    def test_does_not_mutate_caller_supplied_dicts(self):
        """import_scope() must not pop '_type' from the caller's own dict."""
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        record = _exported_working_memory()

        store.import_scope([record], _SCOPE)

        assert record["_type"] == "working_memory"


# ---------------------------------------------------------------------------
# Round-trip fidelity: model_dump(mode="json") + "_type" -> import_scope()
# ---------------------------------------------------------------------------


class TestRoundTripFidelity:
    def test_working_memory_round_trip_preserves_content_and_scope(self):
        original = WorkingMemory(
            id="wm-rt-1",
            agent_id="agent-001",
            tenant_id="t1",
            user_id="user-9",
            content="round trip me",
            metadata={"source": "test"},
            embedding=[0.11, 0.22, 0.33],
            confidence=0.87,
        )
        exported = original.model_dump(mode="json")
        exported["_type"] = "working_memory"
        json.dumps(exported)  # sanity: must already be JSON-safe before re-import

        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="agent-001", tenant_id="t1", user_id="user-9")

        store.import_scope([exported], scope)

        insert_params = pool.cursor.all_params[0]
        # id, tenant_id, agent_id, user_id, thread_id, content, metadata, ...
        assert insert_params[0] == "wm-rt-1"
        assert insert_params[1] == "t1"
        assert insert_params[2] == "agent-001"
        assert insert_params[3] == "user-9"
        assert insert_params[5] == "round trip me"
        assert json.loads(insert_params[6]) == {"source": "test"}

    def test_semantic_fact_round_trip_preserves_confidence(self):
        original = SemanticFact(
            id="fact-rt-1",
            agent_id="agent-001",
            content="user is based in Lisbon",
            confidence=0.72,
        )
        exported = original.model_dump(mode="json")
        exported["_type"] = "semantic_facts"

        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="agent-001")

        store.import_scope([exported], scope)

        # SemanticFactRepository has _DEDUP_ON_WRITE=True, so create() issues a
        # dedup SELECT before the INSERT — the INSERT is the *last* statement.
        insert_sql = pool.cursor.all_sqls[-1]
        insert_params = pool.cursor.all_params[-1]
        assert "INSERT INTO semantic_facts" in insert_sql
        assert insert_params[0] == "fact-rt-1"
        assert insert_params[5] == "user is based in Lisbon"
        assert insert_params[7] == 0.72  # confidence

    def test_entity_profile_round_trip(self):
        original = EntityProfile(
            id="prof-rt-1",
            agent_id="agent-001",
            user_id="user-1",
            content="power Python developer",
        )
        exported = original.model_dump(mode="json")
        exported["_type"] = "entity_profiles"

        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="agent-001", user_id="user-1")

        counts = store.import_scope([exported], scope)

        assert counts["entity_profiles"] == 1
        assert any("INSERT INTO entity_profiles" in s for s in pool.cursor.all_sqls)

    def test_procedural_memory_round_trip(self):
        original = ProceduralMemory(
            id="proc-rt-1",
            agent_id="agent-001",
            content="always check the traceback first",
        )
        exported = original.model_dump(mode="json")
        exported["_type"] = "procedural_memory"

        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="agent-001")

        counts = store.import_scope([exported], scope)

        assert counts["procedural_memory"] == 1

    def test_episodic_memory_round_trip(self):
        original = EpisodicMemory(
            id="ep-rt-1",
            agent_id="agent-001",
            content="on 2026-08-02 the user asked about Db2 vectors",
        )
        exported = original.model_dump(mode="json")
        exported["_type"] = "episodic_memory"

        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        scope = MemoryScope(agent_id="agent-001")

        counts = store.import_scope([exported], scope)

        assert counts["episodic_memory"] == 1

    def test_chunk_round_trip_calls_insert_chunk_with_original_fields(self):
        pool = _CaptureSqlPool()
        store = MemoryStore(pool)
        store.chunks = ChunkRepository(pool)
        scope = MemoryScope(agent_id="agent-001", tenant_id="t1")
        exported = _exported_chunk(tenant_id="t1", agent_id="agent-001")

        store.import_scope([exported], scope)

        insert_sql = pool.cursor.all_sqls[0]
        insert_params = pool.cursor.all_params[0]
        assert "INSERT INTO memory_chunks" in insert_sql
        assert insert_params[1] == "working_memory"  # source_table
        assert insert_params[2] == "wm-import-1"       # source_id
        assert insert_params[3] == 0                   # chunk_index
        assert insert_params[4] == "a chunk of text"    # chunk_text


# ---------------------------------------------------------------------------
# ChunkRepository.list_all() — used internally by export_scope()
# ---------------------------------------------------------------------------


class TestChunkRepositoryListAll:
    def test_list_all_returns_dicts_with_expected_keys(self):
        pool = _FakePool([_chunk_row("chunk-1", "wm-1")])
        repo = ChunkRepository(pool)

        rows = repo.list_all(_SCOPE)

        assert len(rows) == 1
        row = rows[0]
        assert set(row) == {
            "id", "source_table", "source_id", "chunk_index", "chunk_text",
            "embedding", "tenant_id", "agent_id", "user_id", "thread_id",
            "created_at",
        }
        assert row["embedding"] == _VEC
        assert isinstance(row["created_at"], datetime)

    def test_list_all_requires_agent_id(self):
        pool = _FakePool([])
        repo = ChunkRepository(pool)

        with pytest.raises(ValueError, match="agent_id"):
            repo.list_all(MemoryScope(agent_id=""))

    def test_list_all_offset_zero_uses_fetch_first_sql(self):
        pool = _FakePool([_chunk_row("chunk-1", "wm-1")])
        repo = ChunkRepository(pool)

        repo.list_all(_SCOPE, limit=10, offset=0)

        assert "FETCH FIRST ? ROWS ONLY" in pool.cursor.last_sql
        assert "ROW_NUMBER" not in pool.cursor.last_sql

    def test_list_all_offset_positive_uses_row_number_sql(self):
        pool = _FakePool([_chunk_row("chunk-1", "wm-1")])
        repo = ChunkRepository(pool)

        repo.list_all(_SCOPE, limit=10, offset=5)

        assert "ROW_NUMBER" in pool.cursor.last_sql

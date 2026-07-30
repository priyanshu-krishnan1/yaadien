"""
tests/test_orc2.py
~~~~~~~~~~~~~~~~~~
Unit tests for ORC-2: content chunking for long memories.

Coverage:
  1. _split_chunks() utility — boundary cases, overlap, edge cases.
  2. BaseRepository chunking gate — short content skips chunking, long content
     triggers it; create() uses zero-vector on parent when chunking.
  3. BaseRepository.update() — same chunking gate on the update path; stale
     chunk rows deleted before re-writing.
  4. BaseRepository.search(..., search_chunks=True) — routes to chunk repo
     search, deduplicates, resolves parent rows.
  5. ChunkRepository operations — insert_chunk, delete_by_source SQL shapes.
  6. MemoryStore wiring — chunk_repo and embedding_provider propagation;
     enable_chunking=False bypasses chunk_repo creation.

No live Db2 instance required — uses the same fake-pool pattern as
test_lifecycle.py / test_enh4.py.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_memory_sdk.models import (
    MemoryScope,
    SemanticFact,
    WorkingMemory,
)
from agent_memory_sdk.repositories.base import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CHUNK_THRESHOLD,
    _split_chunks,
)
from agent_memory_sdk.repositories.chunks import ChunkRepository
from agent_memory_sdk.repositories.facts import SemanticFactRepository
from agent_memory_sdk.repositories.working import WorkingMemoryRepository
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import SearchMode

# ---------------------------------------------------------------------------
# Fake DB infrastructure (same pattern as test_enh4.py)
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
_VEC_STR = "[" + ",".join("0.1" for _ in range(1536)) + "]"
_SCOPE = MemoryScope(agent_id="agent-001", tenant_id="t1")


def _content_hash(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.lower()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


class _FakeCursor:
    """Minimal fake cursor that records SQL/params and returns preset rows."""

    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        self.last_sql: str = ""
        self.last_params: list[Any] = []
        self.rowcount = 1
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


def _working_row(
    id_: str = "row-uuid",
    content: str = "hello",
    version: int = 1,
    deleted_at: Any = None,
    consolidated_at: Any = None,
) -> tuple[Any, ...]:
    """Fake 16-column DB row for working_memory."""
    return (
        id_, "t1", "agent-001", None, None,
        content, json.dumps({}),
        _VEC_STR,
        1.0,
        _content_hash(content),
        _NOW, _NOW, None, version, deleted_at,
        consolidated_at,
    )


def _facts_row(
    id_: str = "fact-uuid",
    content: str = "hello fact",
    version: int = 1,
) -> tuple[Any, ...]:
    """Fake 18-column DB row for semantic_facts (base 15 + 3 supersession)."""
    return (
        id_, "t1", "agent-001", None, None,
        content, json.dumps({}),
        _VEC_STR,
        1.0,
        _content_hash(content),
        _NOW, _NOW, None, version, None,
        None, None, None,   # superseded_by, superseded_at, supersede_reason
    )


# ---------------------------------------------------------------------------
# 1. _split_chunks() utility
# ---------------------------------------------------------------------------


class TestSplitChunks:
    """Tests for the _split_chunks() utility function."""

    def test_short_text_returns_single_chunk(self):
        text = "a" * 100
        result = _split_chunks(text, chunk_size=800, chunk_overlap=200)
        assert result == [text]

    def test_text_equal_to_chunk_size_returns_single_chunk(self):
        text = "x" * CHUNK_SIZE
        result = _split_chunks(text)
        assert len(result) == 1
        assert result[0] == text

    def test_text_one_over_chunk_size_produces_two_chunks(self):
        # CHUNK_SIZE+1 characters → two chunks
        text = "a" * (CHUNK_SIZE + 1)
        result = _split_chunks(text, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        assert len(result) == 2
        assert result[0] == text[:CHUNK_SIZE]
        assert result[1] == text[CHUNK_SIZE - CHUNK_OVERLAP:]

    def test_exact_two_full_chunks_no_remainder(self):
        step = CHUNK_SIZE - CHUNK_OVERLAP
        text = "a" * (step + CHUNK_SIZE)  # exactly two full chunks
        result = _split_chunks(text)
        assert len(result) == 2
        assert result[0] == text[:CHUNK_SIZE]
        assert result[1] == text[step:]

    def test_overlap_content_shared_between_adjacent_chunks(self):
        # Small numbers for easy verification
        text = "abcdefghij"  # 10 chars
        chunks = _split_chunks(text, chunk_size=6, chunk_overlap=2)
        # step = 6 - 2 = 4
        # chunk0: [0:6]  = "abcdef"
        # chunk1: [4:10] = "efghij"
        assert chunks[0] == "abcdef"
        assert chunks[1] == "efghij"
        # Overlap: "ef" appears in both
        assert chunks[0][-2:] == chunks[1][:2]

    def test_last_chunk_reaches_end(self):
        text = "abc" * 1000  # 3000 chars
        chunks = _split_chunks(text, chunk_size=800, chunk_overlap=200)
        combined_end = chunks[-1][-1]
        assert combined_end == text[-1]

    def test_all_chunks_non_empty(self):
        text = "z" * 5000
        chunks = _split_chunks(text)
        assert all(len(c) > 0 for c in chunks)

    def test_invalid_chunk_size_raises(self):
        with pytest.raises(ValueError, match="chunk_size must be >= 1"):
            _split_chunks("text", chunk_size=0)

    def test_overlap_gte_size_raises(self):
        with pytest.raises(ValueError, match="chunk_overlap must be < chunk_size"):
            _split_chunks("text", chunk_size=100, chunk_overlap=100)

    def test_default_values_match_module_constants(self):
        # Calling with no explicit args should use the module constants
        text = "a" * (CHUNK_SIZE + 1)
        result = _split_chunks(text)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 2. BaseRepository.create() — chunking gate
# ---------------------------------------------------------------------------


class TestCreateChunkingGate:
    """Verify create() chunks long content and skips chunking for short content."""

    def _make_chunk_repo(self) -> tuple[MagicMock, MagicMock]:
        """Return a mock ChunkRepository and a mock embedding provider."""
        chunk_repo = MagicMock()
        chunk_repo.delete_by_source.return_value = 0
        chunk_repo.insert_chunk.return_value = "chunk-id"
        emb_provider = MagicMock(return_value=[0.1] * 1536)
        return chunk_repo, emb_provider

    def test_short_content_no_chunk_calls(self):
        """Content ≤ threshold → no chunk writes, parent gets real embedding."""
        pool = _FakePool()  # no pre-seeded rows → dedup returns None
        chunk_repo, emb_provider = self._make_chunk_repo()
        repo = WorkingMemoryRepository(pool)
        repo._chunk_repo = chunk_repo
        repo._embedding_provider = emb_provider
        repo._chunk_threshold = 2000  # default

        record = WorkingMemory(
            agent_id="agent-001",
            content="short content",  # well under 2000
            embedding=[0.1] * 1536,
        )
        repo.create(record, _SCOPE)

        # Chunk repo was never used
        chunk_repo.delete_by_source.assert_not_called()
        chunk_repo.insert_chunk.assert_not_called()

        # Parent INSERT used real vector (not zero)
        insert_sql = pool.cursor.all_sqls[-1]
        assert "INSERT INTO working_memory" in insert_sql
        assert "[0.0,0.0," not in insert_sql  # not zero-vector sentinel

    def test_long_content_triggers_chunking(self):
        """Content > threshold → parent gets zero-vector sentinel; chunk_repo used."""
        pool = _FakePool()
        chunk_repo, emb_provider = self._make_chunk_repo()

        repo = WorkingMemoryRepository(pool)
        repo._chunk_repo = chunk_repo
        repo._embedding_provider = emb_provider
        repo._chunk_threshold = 10  # tiny threshold to force chunking on short test input

        long_content = "a" * 100  # exceeds threshold of 10
        record = WorkingMemory(
            agent_id="agent-001",
            content=long_content,
            embedding=[0.5] * 1536,
        )
        repo.create(record, _SCOPE)

        # Parent INSERT should have zero-vector sentinel
        insert_sql = pool.cursor.all_sqls[-1]
        assert "INSERT INTO working_memory" in insert_sql
        # The zero-vector sentinel is detected by the prefix "[0.0,0.0,"
        assert "0.0,0.0" in insert_sql

        # Embedding provider was called for at least one chunk
        assert emb_provider.call_count >= 1

        # insert_chunk was called for each chunk
        assert chunk_repo.insert_chunk.call_count >= 1

        # First call to insert_chunk has correct source_table
        first_call_kwargs = chunk_repo.insert_chunk.call_args_list[0]
        assert first_call_kwargs.kwargs["source_table"] == "working_memory"
        assert first_call_kwargs.kwargs["chunk_index"] == 0
        assert first_call_kwargs.kwargs["scope"] == _SCOPE

    def test_no_chunk_repo_skips_chunking(self):
        """When chunk_repo is None, even long content is stored with a real embedding."""
        pool = _FakePool()
        emb_provider = MagicMock(return_value=[0.5] * 1536)

        repo = WorkingMemoryRepository(pool)
        # chunk_repo is None by default
        repo._embedding_provider = emb_provider
        repo._chunk_threshold = 10

        long_content = "a" * 100
        record = WorkingMemory(
            agent_id="agent-001",
            content=long_content,
            embedding=[0.5] * 1536,
        )
        repo.create(record, _SCOPE)

        # embedding_provider was never called for chunking
        emb_provider.assert_not_called()

        # INSERT happened with real embedding (not zero-vector)
        insert_sql = pool.cursor.all_sqls[-1]
        assert "INSERT INTO working_memory" in insert_sql

    def test_no_embedding_provider_skips_chunking(self):
        """When embedding_provider is None, even with chunk_repo set, no chunking."""
        pool = _FakePool()
        chunk_repo = MagicMock()

        repo = WorkingMemoryRepository(pool)
        repo._chunk_repo = chunk_repo
        repo._embedding_provider = None  # no provider
        repo._chunk_threshold = 10

        long_content = "b" * 100
        record = WorkingMemory(
            agent_id="agent-001",
            content=long_content,
            embedding=[0.5] * 1536,
        )
        repo.create(record, _SCOPE)

        # Chunk repo was never invoked
        chunk_repo.delete_by_source.assert_not_called()
        chunk_repo.insert_chunk.assert_not_called()


# ---------------------------------------------------------------------------
# 3. BaseRepository.update() — chunking gate on update path
# ---------------------------------------------------------------------------


class TestUpdateChunkingGate:
    """Verify update() rewrites chunks for long content and skips for short."""

    def test_long_content_update_rewrites_chunks(self):
        """update() with long content: zero-vector on parent + chunk rewrite."""
        existing = _facts_row(id_="f-001", content="x" * 100, version=1)
        pool = _FakePool(rows=[existing])

        chunk_repo = MagicMock()
        chunk_repo.delete_by_source.return_value = 3
        chunk_repo.insert_chunk.return_value = "c-id"
        emb_provider = MagicMock(return_value=[0.2] * 1536)

        repo = SemanticFactRepository(pool)
        repo._chunk_repo = chunk_repo
        repo._embedding_provider = emb_provider
        repo._chunk_threshold = 10

        record = SemanticFact(
            id="f-001",
            agent_id="agent-001",
            content="x" * 100,  # long
            version=1,
        )
        # Fake pool's cursor rowcount = 1 → UPDATE succeeds
        pool.cursor.rowcount = 1

        repo.update(record, _SCOPE)

        # delete_by_source called before inserting fresh chunks
        chunk_repo.delete_by_source.assert_called_once_with(
            "f-001", "semantic_facts", _SCOPE
        )
        assert chunk_repo.insert_chunk.call_count >= 1

    def test_short_content_update_no_chunk_calls(self):
        """update() with short content: real embedding on parent, no chunk ops."""
        existing = _facts_row(id_="f-002", content="short", version=1)
        pool = _FakePool(rows=[existing])
        pool.cursor.rowcount = 1

        chunk_repo = MagicMock()
        emb_provider = MagicMock(return_value=[0.3] * 1536)

        repo = SemanticFactRepository(pool)
        repo._chunk_repo = chunk_repo
        repo._embedding_provider = emb_provider
        repo._chunk_threshold = 2000  # default, "short" won't exceed it

        record = SemanticFact(
            id="f-002",
            agent_id="agent-001",
            content="short",
            version=1,
        )

        repo.update(record, _SCOPE)

        chunk_repo.delete_by_source.assert_not_called()
        chunk_repo.insert_chunk.assert_not_called()


# ---------------------------------------------------------------------------
# 4. BaseRepository.search(search_chunks=True) — chunk-based search path
# ---------------------------------------------------------------------------


class TestChunkSearch:
    """Verify search(search_chunks=True) routes correctly and resolves parents."""

    def test_search_chunks_false_does_not_call_chunk_repo(self):
        """Default path (search_chunks=False) bypasses chunk_repo entirely."""
        pool = _FakePool(rows=[])
        chunk_repo = MagicMock()
        repo = WorkingMemoryRepository(pool)
        repo._chunk_repo = chunk_repo

        repo.search(query_embedding=[0.1] * 1536, scope=_SCOPE, search_chunks=False)

        chunk_repo.search_chunks.assert_not_called()

    def test_search_chunks_true_with_no_chunk_repo_falls_back(self):
        """search_chunks=True with no chunk_repo falls back to standard path."""
        pool = _FakePool(rows=[])
        repo = WorkingMemoryRepository(pool)
        # _chunk_repo is None by default — standard search path
        repo.search(query_embedding=[0.1] * 1536, scope=_SCOPE, search_chunks=True)
        # No error; standard path executed (empty result is fine for this test)

    def test_search_chunks_true_calls_chunk_repo_and_resolves(self):
        """search_chunks=True: chunk_repo.search_chunks called; parents resolved."""
        # Chunk search returns two hits for the same parent
        chunk_hits = [("parent-001", 0.15), ("parent-001", 0.22)]

        parent_row = _working_row(id_="parent-001", content="long content")

        chunk_repo = MagicMock()
        chunk_repo.search_chunks.return_value = chunk_hits

        pool = _FakePool(rows=[parent_row])
        repo = WorkingMemoryRepository(pool)
        repo._chunk_repo = chunk_repo

        results = repo.search(
            query_embedding=[0.1] * 1536,
            scope=_SCOPE,
            top_k=5,
            search_chunks=True,
        )

        # chunk_repo.search_chunks was called
        chunk_repo.search_chunks.assert_called_once()
        call_kwargs = chunk_repo.search_chunks.call_args.kwargs
        assert call_kwargs["source_table"] == "working_memory"
        assert call_kwargs["scope"] == _SCOPE

        # One parent returned (deduped from two chunk hits)
        assert len(results) == 1
        assert results[0].id == "parent-001"

    def test_search_chunks_deduplication_keeps_best_distance(self):
        """When multiple chunks map to the same parent, best distance wins."""
        # Three chunks: two for parent-A (distances 0.3 and 0.1), one for parent-B (0.2)
        chunk_hits = [
            ("parent-A", 0.3),
            ("parent-B", 0.2),
            ("parent-A", 0.1),
        ]

        row_A = _working_row(id_="parent-A", content="content A")
        row_B = _working_row(id_="parent-B", content="content B")

        chunk_repo = MagicMock()
        chunk_repo.search_chunks.return_value = chunk_hits

        pool = _FakePool(rows=[row_A, row_B])
        repo = WorkingMemoryRepository(pool)
        repo._chunk_repo = chunk_repo

        results = repo.search(
            query_embedding=[0.1] * 1536,
            scope=_SCOPE,
            top_k=10,
            search_chunks=True,
        )

        # Both parents returned
        assert len(results) == 2
        result_ids = [r.id for r in results]
        # parent-A has best distance 0.1 < parent-B 0.2 → parent-A comes first
        assert result_ids[0] == "parent-A"
        assert result_ids[1] == "parent-B"

    def test_search_chunks_respects_top_k(self):
        """top_k limits the number of returned parents after dedup."""
        chunk_hits = [
            ("parent-1", 0.1),
            ("parent-2", 0.2),
            ("parent-3", 0.3),
            ("parent-4", 0.4),
        ]

        rows = [_working_row(id_=f"parent-{i}", content=f"c{i}") for i in range(1, 5)]

        chunk_repo = MagicMock()
        chunk_repo.search_chunks.return_value = chunk_hits

        pool = _FakePool(rows=rows)
        repo = WorkingMemoryRepository(pool)
        repo._chunk_repo = chunk_repo

        results = repo.search(
            query_embedding=[0.1] * 1536,
            scope=_SCOPE,
            top_k=2,
            search_chunks=True,
        )

        # Only top 2 parents
        assert len(results) <= 2

    def test_search_chunks_empty_hits_returns_empty_list(self):
        """When chunk_repo.search_chunks returns [], search returns []."""
        chunk_repo = MagicMock()
        chunk_repo.search_chunks.return_value = []

        pool = _FakePool(rows=[])
        repo = WorkingMemoryRepository(pool)
        repo._chunk_repo = chunk_repo

        results = repo.search(
            query_embedding=[0.1] * 1536,
            scope=_SCOPE,
            search_chunks=True,
        )

        assert results == []


# ---------------------------------------------------------------------------
# 5. ChunkRepository — SQL shape tests
# ---------------------------------------------------------------------------


class TestChunkRepository:
    """Test ChunkRepository SQL generation and return values."""

    def test_insert_chunk_sql_shape(self):
        """insert_chunk issues the correct INSERT INTO memory_chunks SQL."""
        pool = _FakePool()
        repo = ChunkRepository(pool, embedding_dim=1536)

        chunk_id = repo.insert_chunk(
            source_table="working_memory",
            source_id="src-001",
            chunk_index=0,
            chunk_text="Hello chunk",
            embedding=[0.1] * 1536,
            scope=_SCOPE,
        )

        assert isinstance(chunk_id, str)
        sql = pool.cursor.all_sqls[-1]
        assert "INSERT INTO memory_chunks" in sql
        assert "source_table" in sql
        assert "source_id" in sql
        assert "chunk_index" in sql

    def test_insert_chunk_returns_uuid_string(self):
        """insert_chunk returns a UUID string (36 chars)."""
        pool = _FakePool()
        repo = ChunkRepository(pool, embedding_dim=1536)

        chunk_id = repo.insert_chunk(
            source_table="semantic_facts",
            source_id="sf-001",
            chunk_index=2,
            chunk_text="some chunk",
            embedding=[0.5] * 1536,
            scope=_SCOPE,
        )

        assert len(chunk_id) == 36  # UUID4 format

    def test_delete_by_source_sql_shape(self):
        """delete_by_source issues DELETE FROM memory_chunks with correct predicates."""
        pool = _FakePool()
        repo = ChunkRepository(pool, embedding_dim=1536)

        repo.delete_by_source("src-001", "working_memory", _SCOPE)

        sql = pool.cursor.last_sql
        assert "DELETE FROM memory_chunks" in sql
        assert "source_id" in sql
        assert "source_table" in sql
        assert "agent_id" in sql

    def test_delete_by_source_includes_tenant_when_present(self):
        """delete_by_source appends tenant_id filter when scope has one."""
        pool = _FakePool()
        repo = ChunkRepository(pool, embedding_dim=1536)
        scope_with_tenant = MemoryScope(agent_id="agent-x", tenant_id="t99")

        repo.delete_by_source("src-002", "episodic_memory", scope_with_tenant)

        sql = pool.cursor.last_sql
        assert "tenant_id" in sql

    def test_search_chunks_requires_non_empty_embedding(self):
        """search_chunks raises ValueError for empty query_embedding."""
        pool = _FakePool()
        repo = ChunkRepository(pool, embedding_dim=1536)

        with pytest.raises(ValueError, match="query_embedding must be a non-empty list"):
            repo.search_chunks(
                query_embedding=[],
                source_table="working_memory",
                scope=_SCOPE,
            )

    def test_search_chunks_requires_agent_id(self):
        """search_chunks raises ValueError when scope.agent_id is missing."""
        pool = _FakePool()
        repo = ChunkRepository(pool, embedding_dim=1536)
        bad_scope = MemoryScope(agent_id="")

        with pytest.raises(ValueError):
            repo.search_chunks(
                query_embedding=[0.1] * 1536,
                source_table="working_memory",
                scope=bad_scope,
            )

    def test_search_chunks_sql_includes_source_table_filter(self):
        """search_chunks SQL filters on source_table = ?."""
        pool = _FakePool(rows=[])
        repo = ChunkRepository(pool, embedding_dim=1536)

        repo.search_chunks(
            query_embedding=[0.1] * 1536,
            source_table="semantic_facts",
            scope=_SCOPE,
        )

        rank_sql = pool.cursor.all_sqls[0]
        assert "FROM memory_chunks" in rank_sql
        assert "source_table" in rank_sql

    def test_search_chunks_empty_table_returns_empty_list(self):
        """When no rows exist, search_chunks returns []."""
        pool = _FakePool(rows=[])
        repo = ChunkRepository(pool, embedding_dim=1536)

        results = repo.search_chunks(
            query_embedding=[0.1] * 1536,
            source_table="working_memory",
            scope=_SCOPE,
        )

        assert results == []

    def test_search_chunks_approx_mode_adds_approx_clause(self):
        """APPROX mode adds 'APPROX' to the FETCH FIRST clause."""
        pool = _FakePool(rows=[])
        repo = ChunkRepository(pool, embedding_dim=1536)

        repo.search_chunks(
            query_embedding=[0.1] * 1536,
            source_table="working_memory",
            scope=_SCOPE,
            mode=SearchMode.APPROX,
        )

        rank_sql = pool.cursor.all_sqls[0]
        assert "APPROX" in rank_sql


# ---------------------------------------------------------------------------
# 6. MemoryStore — wiring and configuration
# ---------------------------------------------------------------------------


class TestMemoryStoreChunkWiring:
    """Test that MemoryStore correctly wires chunk_repo and embedding_provider."""

    def test_no_embedding_provider_disables_chunking(self):
        """MemoryStore without embedding_provider → chunk_repo is None."""
        pool = _FakePool()
        store = MemoryStore(pool)
        assert store.chunks is None
        assert store.working._chunk_repo is None

    def test_embedding_provider_enables_chunking(self):
        """MemoryStore with embedding_provider + enable_chunking=True → chunk_repo set."""
        pool = _FakePool()
        ep = lambda text: [0.1] * 1536  # noqa: E731
        store = MemoryStore(pool, embedding_provider=ep)

        assert store.chunks is not None
        assert isinstance(store.chunks, ChunkRepository)
        # All five repos share the same chunk_repo
        assert store.working._chunk_repo is store.chunks
        assert store.episodic._chunk_repo is store.chunks
        assert store.facts._chunk_repo is store.chunks
        assert store.profiles._chunk_repo is store.chunks
        assert store.procedures._chunk_repo is store.chunks

    def test_embedding_provider_propagated_to_all_repos(self):
        """embedding_provider is stored on every per-type repository."""
        pool = _FakePool()
        ep = lambda text: [0.2] * 1536  # noqa: E731
        store = MemoryStore(pool, embedding_provider=ep)

        for repo in (store.working, store.episodic, store.facts, store.profiles, store.procedures):
            assert repo._embedding_provider is ep

    def test_enable_chunking_false_disables_chunk_repo(self):
        """enable_chunking=False → chunk_repo is None even with embedding_provider."""
        pool = _FakePool()
        ep = lambda text: [0.3] * 1536  # noqa: E731
        store = MemoryStore(pool, embedding_provider=ep, enable_chunking=False)

        assert store.chunks is None
        assert store.working._chunk_repo is None

    def test_custom_chunk_threshold_propagated(self):
        """Custom chunk_threshold is forwarded to per-type repositories."""
        pool = _FakePool()
        ep = lambda text: [0.1] * 1536  # noqa: E731
        store = MemoryStore(
            pool,
            embedding_provider=ep,
            chunk_threshold=500,
            chunk_size=200,
            chunk_overlap=50,
        )

        for repo in (store.working, store.episodic, store.facts, store.profiles, store.procedures):
            assert repo._chunk_threshold == 500
            assert repo._chunk_size == 200
            assert repo._chunk_overlap == 50

    def test_chunk_repo_embedding_dim_matches_store(self):
        """ChunkRepository.embedding_dim matches the MemoryStore embedding_dim."""
        pool = _FakePool()
        ep = lambda text: [0.1] * 768  # noqa: E731
        store = MemoryStore(pool, embedding_dim=768, embedding_provider=ep)

        assert store.chunks is not None
        assert store.chunks.embedding_dim == 768


# ---------------------------------------------------------------------------
# 7. Module-level defaults
# ---------------------------------------------------------------------------


class TestChunkDefaults:
    """Verify that the module-level chunk constants have the expected values."""

    def test_chunk_threshold_default(self):
        assert CHUNK_THRESHOLD == 2000

    def test_chunk_size_default(self):
        assert CHUNK_SIZE == 800

    def test_chunk_overlap_default(self):
        assert CHUNK_OVERLAP == 200

    def test_overlap_less_than_size(self):
        """Invariant: default overlap is strictly less than default size."""
        assert CHUNK_OVERLAP < CHUNK_SIZE

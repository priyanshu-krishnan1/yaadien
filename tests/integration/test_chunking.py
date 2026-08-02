"""
tests/integration/test_chunking.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Integration tests for LIVE-5: content-chunking repository (Epic-10).

Covers:
- Insert / list round-trip: 4 chunks for a single source document, ordered
  by chunk_index ascending.
- Pagination: list_all(limit=2, offset=0) vs offset=2.
- Vector search: nearest-neighbour correctness using deterministic unit
  vectors; verifies the hot-index chunk ranks first and no cross-scope
  leakage occurs.
- delete_by_source: targeted deletion removes only source_A chunks while
  source_B chunks remain untouched.
- erase_by_scope: full scope erasure removes all chunks for scope_A but
  leaves scope_B chunks intact.

Requires: DB2_DATABASE and companion DB2_* env vars, or a .env file.
See project-management/INTEGRATION_TESTING.md.
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration.conftest import make_unit_vec

pytestmark = pytest.mark.integration

# Source-table discriminator used across all tests in this module
_SOURCE_TABLE = "semantic_facts"
# Embedding dimension matching the schema (must equal the DDL VECTOR size)
_DIM = 1536


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(migrated_pool):
    """Construct a ChunkRepository against the live migrated database."""
    from agent_memory_sdk.repositories.chunks import ChunkRepository

    return ChunkRepository(pool=migrated_pool, embedding_dim=_DIM)


def _fresh_scope(agent_id: str | None = None):
    """Return a MemoryScope with a unique agent_id for per-call isolation."""
    from agent_memory_sdk.models import MemoryScope

    aid = agent_id or f"test-chunk-agent-{uuid.uuid4()}"
    return MemoryScope(agent_id=aid, user_id="test-user-chunks")


# ---------------------------------------------------------------------------
# LIVE-5: ChunkRepository round-trips
# ---------------------------------------------------------------------------


class TestChunkRoundTrip:
    """Insert → list_all round-trip for a single source document."""

    def test_insert_four_chunks_all_listed(self, migrated_pool, unique_agent_id):
        """Inserting 4 chunks for one source doc returns all 4 via list_all_for_scope."""
        repo = _make_repo(migrated_pool)
        scope = _fresh_scope(unique_agent_id)
        source_id = f"doc-{uuid.uuid4()}"

        inserted_ids = []
        for i in range(4):
            cid = repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id,
                chunk_index=i,
                chunk_text=f"chunk text {i}",
                embedding=make_unit_vec(_DIM, i),
                scope=scope,
            )
            assert cid, f"insert_chunk must return a non-empty id for chunk_index={i}"
            inserted_ids.append(cid)

        chunks = repo.list_all_for_scope(scope)
        retrieved_ids = [c["id"] for c in chunks]

        for cid in inserted_ids:
            assert cid in retrieved_ids, (
                f"Inserted chunk {cid} must appear in list_all_for_scope result"
            )
        assert len(chunks) == 4, (
            f"Expected 4 chunks, got {len(chunks)}"
        )

    def test_chunks_ordered_by_chunk_index_ascending(self, migrated_pool, unique_agent_id):
        """list_all_for_scope must return chunks ordered by chunk_index ascending."""
        repo = _make_repo(migrated_pool)
        scope = _fresh_scope(unique_agent_id)
        source_id = f"doc-order-{uuid.uuid4()}"

        # Insert in reverse order to confirm the DB sorts, not insertion order
        for i in reversed(range(4)):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id,
                chunk_index=i,
                chunk_text=f"reversed chunk {i}",
                embedding=make_unit_vec(_DIM, i),
                scope=scope,
            )

        chunks = repo.list_all_for_scope(scope)
        assert len(chunks) == 4
        indices = [c["chunk_index"] for c in chunks]
        assert indices == sorted(indices), (
            f"Chunks must be ordered by chunk_index ascending; got {indices}"
        )


# ---------------------------------------------------------------------------
# LIVE-5: Pagination
# ---------------------------------------------------------------------------


class TestChunkPagination:
    """list_all(limit, offset) pagination correctness."""

    def test_pagination_first_page(self, migrated_pool, unique_agent_id):
        """list_all(limit=2, offset=0) must return the first 2 chunks."""
        repo = _make_repo(migrated_pool)
        scope = _fresh_scope(unique_agent_id)
        source_id = f"doc-page-{uuid.uuid4()}"

        for i in range(4):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id,
                chunk_index=i,
                chunk_text=f"page chunk {i}",
                embedding=make_unit_vec(_DIM, i),
                scope=scope,
            )

        page1 = repo.list_all(scope, limit=2, offset=0)
        assert len(page1) == 2, (
            f"list_all(limit=2, offset=0) must return exactly 2 rows; got {len(page1)}"
        )
        assert page1[0]["chunk_index"] == 0
        assert page1[1]["chunk_index"] == 1

    def test_pagination_second_page(self, migrated_pool, unique_agent_id):
        """list_all(limit=2, offset=2) must return the remaining 2 chunks."""
        repo = _make_repo(migrated_pool)
        scope = _fresh_scope(unique_agent_id)
        source_id = f"doc-page2-{uuid.uuid4()}"

        for i in range(4):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id,
                chunk_index=i,
                chunk_text=f"page2 chunk {i}",
                embedding=make_unit_vec(_DIM, i),
                scope=scope,
            )

        page2 = repo.list_all(scope, limit=2, offset=2)
        assert len(page2) == 2, (
            f"list_all(limit=2, offset=2) must return exactly 2 rows; got {len(page2)}"
        )
        assert page2[0]["chunk_index"] == 2
        assert page2[1]["chunk_index"] == 3

    def test_pagination_pages_are_disjoint(self, migrated_pool, unique_agent_id):
        """The two pages must cover all 4 chunks with no overlap."""
        repo = _make_repo(migrated_pool)
        scope = _fresh_scope(unique_agent_id)
        source_id = f"doc-disjoint-{uuid.uuid4()}"

        for i in range(4):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id,
                chunk_index=i,
                chunk_text=f"disjoint chunk {i}",
                embedding=make_unit_vec(_DIM, i),
                scope=scope,
            )

        page1 = repo.list_all(scope, limit=2, offset=0)
        page2 = repo.list_all(scope, limit=2, offset=2)

        ids1 = {c["id"] for c in page1}
        ids2 = {c["id"] for c in page2}

        assert ids1.isdisjoint(ids2), "Page 1 and page 2 must not share any chunk ids"
        assert len(ids1 | ids2) == 4, "Together the two pages must cover all 4 chunks"


# ---------------------------------------------------------------------------
# LIVE-5: Vector search over chunks
# ---------------------------------------------------------------------------


class TestChunkVectorSearch:
    """search_chunks nearest-neighbour correctness and scope isolation."""

    def test_nearest_neighbour_ranks_first(self, migrated_pool, unique_agent_id):
        """Chunk with hot_index=5 must be the nearest neighbour for query hot_index=5."""
        repo = _make_repo(migrated_pool)
        scope = _fresh_scope(unique_agent_id)
        source_id = f"doc-nn-{uuid.uuid4()}"

        # chunk_index=1 gets hot_index=5; others get distinct, non-overlapping indices
        hot_index = 5
        embeddings = [10, hot_index, 20, 30]  # hot_index at position 1
        inserted_source_ids: list[str] = []
        for i, hot in enumerate(embeddings):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id,
                chunk_index=i,
                chunk_text=f"nn chunk {i}",
                embedding=make_unit_vec(_DIM, hot),
                scope=scope,
            )
            inserted_source_ids.append(source_id)

        query = make_unit_vec(_DIM, hot_index)
        results = repo.search_chunks(
            query_embedding=query,
            source_table=_SOURCE_TABLE,
            scope=scope,
            top_n=4,
        )

        assert results, "search_chunks must return at least one result"
        # results is list[tuple[source_id, distance]] — nearest first
        nearest_source_id, nearest_distance = results[0]
        assert nearest_source_id == source_id, (
            "The nearest chunk must belong to the correct source_id"
        )
        # Cosine distance for identical unit vectors = 0.0 (nearest)
        assert nearest_distance < 0.01, (
            f"Expected near-zero cosine distance for the matching hot_index, got {nearest_distance}"
        )

    def test_no_cross_scope_leakage(self, migrated_pool, unique_agent_id):
        """search_chunks must not return chunks belonging to a different scope."""
        repo = _make_repo(migrated_pool)

        # scope_A owns the chunk we search for
        scope_a = _fresh_scope(unique_agent_id)
        source_id_a = f"doc-scope-a-{uuid.uuid4()}"
        repo.insert_chunk(
            source_table=_SOURCE_TABLE,
            source_id=source_id_a,
            chunk_index=0,
            chunk_text="scope A chunk",
            embedding=make_unit_vec(_DIM, 7),
            scope=scope_a,
        )

        # scope_B owns a chunk with the same hot_index — it must never appear in scope_A results
        scope_b = _fresh_scope()
        source_id_b = f"doc-scope-b-{uuid.uuid4()}"
        repo.insert_chunk(
            source_table=_SOURCE_TABLE,
            source_id=source_id_b,
            chunk_index=0,
            chunk_text="scope B chunk",
            embedding=make_unit_vec(_DIM, 7),
            scope=scope_b,
        )

        query = make_unit_vec(_DIM, 7)
        results_a = repo.search_chunks(
            query_embedding=query,
            source_table=_SOURCE_TABLE,
            scope=scope_a,
            top_n=10,
        )

        returned_source_ids = {r[0] for r in results_a}
        assert source_id_b not in returned_source_ids, (
            "search_chunks must not return chunks from a different scope"
        )
        assert source_id_a in returned_source_ids, (
            "search_chunks must return the chunk belonging to the queried scope"
        )


# ---------------------------------------------------------------------------
# LIVE-5: delete_by_source — targeted deletion
# ---------------------------------------------------------------------------


class TestDeleteBySource:
    """delete_by_source removes only the targeted source's chunks."""

    def test_delete_returns_correct_count(self, migrated_pool, unique_agent_id):
        """delete_by_source must return the exact number of deleted rows."""
        repo = _make_repo(migrated_pool)
        scope = _fresh_scope(unique_agent_id)
        source_id_a = f"doc-del-a-{uuid.uuid4()}"
        source_id_b = f"doc-del-b-{uuid.uuid4()}"

        # Insert 3 chunks for source_A and 2 for source_B
        for i in range(3):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id_a,
                chunk_index=i,
                chunk_text=f"del-A chunk {i}",
                embedding=make_unit_vec(_DIM, i),
                scope=scope,
            )
        for i in range(2):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id_b,
                chunk_index=i,
                chunk_text=f"del-B chunk {i}",
                embedding=make_unit_vec(_DIM, i + 50),
                scope=scope,
            )

        deleted = repo.delete_by_source(
            source_id=source_id_a,
            source_table=_SOURCE_TABLE,
            scope=scope,
        )
        assert deleted == 3, (
            f"delete_by_source must return 3 (number of source_A chunks); got {deleted}"
        )

    def test_delete_removes_source_a_chunks(self, migrated_pool, unique_agent_id):
        """After delete_by_source(source_A), list_all_for_scope must not contain source_A."""
        repo = _make_repo(migrated_pool)
        scope = _fresh_scope(unique_agent_id)
        source_id_a = f"doc-gone-a-{uuid.uuid4()}"
        source_id_b = f"doc-kept-b-{uuid.uuid4()}"

        for i in range(3):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id_a,
                chunk_index=i,
                chunk_text=f"gone-A {i}",
                embedding=make_unit_vec(_DIM, i + 100),
                scope=scope,
            )
        for i in range(2):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id_b,
                chunk_index=i,
                chunk_text=f"kept-B {i}",
                embedding=make_unit_vec(_DIM, i + 200),
                scope=scope,
            )

        repo.delete_by_source(
            source_id=source_id_a,
            source_table=_SOURCE_TABLE,
            scope=scope,
        )

        remaining = repo.list_all_for_scope(scope)
        remaining_source_ids = {c["source_id"] for c in remaining}

        assert source_id_a not in remaining_source_ids, (
            "source_A chunks must be gone after delete_by_source(source_A)"
        )

    def test_delete_source_a_leaves_source_b_intact(self, migrated_pool, unique_agent_id):
        """delete_by_source(source_A) must leave source_B chunks untouched."""
        repo = _make_repo(migrated_pool)
        scope = _fresh_scope(unique_agent_id)
        source_id_a = f"doc-del2-a-{uuid.uuid4()}"
        source_id_b = f"doc-del2-b-{uuid.uuid4()}"

        for i in range(2):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id_a,
                chunk_index=i,
                chunk_text=f"del2-A {i}",
                embedding=make_unit_vec(_DIM, i + 300),
                scope=scope,
            )
        b_ids = []
        for i in range(3):
            cid = repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id_b,
                chunk_index=i,
                chunk_text=f"del2-B {i}",
                embedding=make_unit_vec(_DIM, i + 400),
                scope=scope,
            )
            b_ids.append(cid)

        repo.delete_by_source(
            source_id=source_id_a,
            source_table=_SOURCE_TABLE,
            scope=scope,
        )

        remaining = repo.list_all_for_scope(scope)
        remaining_ids = {c["id"] for c in remaining}

        for bid in b_ids:
            assert bid in remaining_ids, (
                f"source_B chunk {bid} must still exist after delete_by_source(source_A)"
            )
        assert len(remaining) == 3, (
            f"Exactly 3 source_B chunks must remain; got {len(remaining)}"
        )


# ---------------------------------------------------------------------------
# LIVE-5: erase_by_scope — full scope erasure
# ---------------------------------------------------------------------------


class TestEraseByScope:
    """erase_by_scope removes all chunks for a scope without touching other scopes."""

    def test_erase_returns_positive_count(self, migrated_pool):
        """erase_by_scope must return a positive row count when chunks exist."""
        repo = _make_repo(migrated_pool)
        scope_a = _fresh_scope()
        source_id = f"doc-erase-{uuid.uuid4()}"

        for i in range(3):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id,
                chunk_index=i,
                chunk_text=f"erase chunk {i}",
                embedding=make_unit_vec(_DIM, i + 500),
                scope=scope_a,
            )

        deleted = repo.erase_by_scope(scope_a)
        assert deleted > 0, (
            f"erase_by_scope must return a positive count when chunks exist; got {deleted}"
        )

    def test_erase_scope_a_clears_all_its_chunks(self, migrated_pool):
        """list_all_for_scope(scope_A) must be empty after erase_by_scope(scope_A)."""
        repo = _make_repo(migrated_pool)
        scope_a = _fresh_scope()
        source_id = f"doc-eraseA-{uuid.uuid4()}"

        for i in range(4):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id,
                chunk_index=i,
                chunk_text=f"eraseA chunk {i}",
                embedding=make_unit_vec(_DIM, i + 600),
                scope=scope_a,
            )

        repo.erase_by_scope(scope_a)

        remaining = repo.list_all_for_scope(scope_a)
        assert remaining == [], (
            f"list_all_for_scope(scope_A) must be empty after erase_by_scope; got {remaining}"
        )

    def test_erase_scope_a_leaves_scope_b_intact(self, migrated_pool):
        """erase_by_scope(scope_A) must not delete chunks belonging to scope_B."""
        repo = _make_repo(migrated_pool)
        scope_a = _fresh_scope()
        scope_b = _fresh_scope()

        source_id_a = f"doc-eraseA2-{uuid.uuid4()}"
        source_id_b = f"doc-eraseB2-{uuid.uuid4()}"

        for i in range(3):
            repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id_a,
                chunk_index=i,
                chunk_text=f"eraseA2 chunk {i}",
                embedding=make_unit_vec(_DIM, i + 700),
                scope=scope_a,
            )

        b_ids = []
        for i in range(3):
            cid = repo.insert_chunk(
                source_table=_SOURCE_TABLE,
                source_id=source_id_b,
                chunk_index=i,
                chunk_text=f"eraseB2 chunk {i}",
                embedding=make_unit_vec(_DIM, i + 800),
                scope=scope_b,
            )
            b_ids.append(cid)

        repo.erase_by_scope(scope_a)

        remaining_b = repo.list_all_for_scope(scope_b)
        remaining_b_ids = {c["id"] for c in remaining_b}

        for bid in b_ids:
            assert bid in remaining_b_ids, (
                f"scope_B chunk {bid} must survive erase_by_scope(scope_A)"
            )
        assert len(remaining_b) == 3, (
            f"All 3 scope_B chunks must remain after erase_by_scope(scope_A); got {len(remaining_b)}"
        )

"""
benchmarks/write/test_chunking.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-8 / A5 — Benchmark chunking (ORC-2) across content sizes.

Content sizes tested: 200, 1000, 5000, 20000, 60000 characters.

The ``CHUNK_THRESHOLD`` is 2000 characters.  Tests crossing that boundary
verify:
  - content ≤ 2000:  single parent row, no chunk rows written.
  - content > 2000:  parent row gets a zero-vector sentinel; chunk rows are
                     written to ``memory_chunks``.

Round-trip counts are recorded via ``counting_pool`` + ``round_trips``.
Because the chunk write calls insert_chunk per chunk, the INSERT count
for large content equals 1 (parent INSERT) + N (chunk INSERTs) plus the
preceding dedup SELECT (since SemanticFact has ``_DEDUP_ON_WRITE=True``).

For WorkingMemory (dedup OFF), counts are 1 (parent INSERT) + N (chunks).

This test uses WorkingMemory because its dedup-OFF path is simpler to reason
about — only 1 parent execute regardless of content size.

Marker: ``benchmark_pr`` (Tier 1 — requires Db2).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from agent_memory_sdk.models import MemoryScope, WorkingMemory
from agent_memory_sdk.repositories.base import CHUNK_THRESHOLD
from agent_memory_sdk.repositories.chunks import ChunkRepository
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler
from benchmarks.common.counting import CountingPool, RoundTripsFixture
from benchmarks.common.embedding_providers import HashingEmbeddingProvider

pytestmark = pytest.mark.benchmark_pr

# ---------------------------------------------------------------------------
# Content size parametrization
# ---------------------------------------------------------------------------

_CONTENT_SIZES = [200, 1000, 5000, 20000, 60000]

_BENCH_TENANT = "bm8-a5"


def _make_content(size: int) -> str:
    """Return a deterministic string of exactly *size* characters."""
    base = "abcdefghijklmnopqrstuvwxyz 0123456789 "
    repeats = (size // len(base)) + 1
    return (base * repeats)[:size]


def _make_scope(run_id: str, size: int) -> MemoryScope:
    return MemoryScope(
        tenant_id=f"{_BENCH_TENANT}-{run_id}",
        agent_id=f"bm8-chunk-{size}-{run_id}",
    )


# ---------------------------------------------------------------------------
# Round-trip count + structural assertions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("content_size", _CONTENT_SIZES, ids=[str(s) for s in _CONTENT_SIZES])
def test_chunking_round_trips_and_structure(
    db_pool: Any,
    counting_pool: CountingPool,
    round_trips: RoundTripsFixture,
    content_size: int,
) -> None:
    """Assert chunk rows written and round-trip counts for each content size.

    For content ≤ CHUNK_THRESHOLD:
      - No chunk rows in memory_chunks.
      - Parent INSERT: 1 execute (WorkingMemory dedup OFF).

    For content > CHUNK_THRESHOLD:
      - Chunk rows are written to memory_chunks (one per chunk window).
      - Execute count = 1 (parent INSERT)
                      + 1 (delete_by_source for stale chunks, even on first create)
                      + N (chunk INSERTs, one per chunk).
    """
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope(run_id, content_size)
    content = _make_content(content_size)

    embedding_provider = HashingEmbeddingProvider()

    store = MemoryStore(
        pool=counting_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=embedding_provider,
        enable_chunking=True,
        chunk_threshold=CHUNK_THRESHOLD,
        chunk_size=800,
        chunk_overlap=200,
    )

    try:
        record = WorkingMemory(agent_id=scope.agent_id, content=content)
        round_trips.reset()
        stored = store.remember(record, scope)
        executes_after = round_trips.counter.executes

        # Verify structural properties via the chunk repository.
        chunk_repo = ChunkRepository(db_pool)
        chunks = chunk_repo.list_all(scope)
        chunk_count = len(chunks)

        if content_size <= CHUNK_THRESHOLD:
            # Short content: no chunking — single parent row, no chunks.
            assert chunk_count == 0, (
                f"Expected 0 chunk rows for content_size={content_size} "
                f"(≤ CHUNK_THRESHOLD={CHUNK_THRESHOLD}), got {chunk_count}."
            )
            # WorkingMemory dedup OFF: exactly 1 execute (the parent INSERT).
            assert executes_after == 1, (
                f"Expected 1 execute for short content (size={content_size}), "
                f"got {executes_after}."
            )
        else:
            # Long content: chunk rows must be present.
            assert chunk_count > 0, (
                f"Expected >0 chunk rows for content_size={content_size} "
                f"(> CHUNK_THRESHOLD={CHUNK_THRESHOLD}), got 0."
            )
            # All chunks reference the stored parent row.
            for chunk in chunks:
                assert chunk["source_id"] == stored.id, (
                    f"Chunk source_id {chunk['source_id']!r} != parent id {stored.id!r}."
                )

    finally:
        store.erase_all(scope)


# ---------------------------------------------------------------------------
# Latency benchmarks — one benchmark per content size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("content_size", _CONTENT_SIZES, ids=[str(s) for s in _CONTENT_SIZES])
def test_benchmark_chunking(benchmark: Any, db_pool: Any, content_size: int) -> None:
    """Benchmark remember() end-to-end latency per content size.

    This captures the combined cost of:
    - Parent INSERT (always 1 round-trip for WorkingMemory).
    - ORC-2 chunk write path (only when content > CHUNK_THRESHOLD):
        delete_by_source + N chunk INSERTs.
    """
    run_id = uuid.uuid4().hex[:8]
    scope = _make_scope(run_id, content_size)
    content = _make_content(content_size)

    embedding_provider = HashingEmbeddingProvider()

    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        embedding_provider=embedding_provider,
        enable_chunking=True,
        chunk_threshold=CHUNK_THRESHOLD,
        chunk_size=800,
        chunk_overlap=200,
    )

    iteration_counter = [0]

    def _remember_once() -> None:
        # Use unique content per iteration to avoid dedup short-circuit in
        # other record types; WorkingMemory (dedup OFF) always inserts anyway.
        run_content = content + f" iter={iteration_counter[0]}"
        iteration_counter[0] += 1
        rec = WorkingMemory(agent_id=scope.agent_id, content=run_content)
        store.remember(rec, scope)

    try:
        benchmark(_remember_once)
    finally:
        store.erase_all(scope)

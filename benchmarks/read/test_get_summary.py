"""
benchmarks/read/test_get_summary.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-9 / B12: ``get_summary()`` across thread lengths 10 / 100 / 1 000 turns.

``get_summary()`` performs a single ``working.list_all(limit=10000)`` call and
then formats the result in Python — no LLM, no embedding.  The benchmark
measures:
  * DB fetch time (varies with thread length due to row count)
  * Python formatting time (included in the same call — hard to separate
    without patching, so we time the full call and note this in extra_info)

embed_ms is always 0 (no embedding call).

Thread lengths seeded:
  * 10 turns   — small thread, near-zero fetch
  * 100 turns  — medium thread
  * 1 000 turns — large thread (exercises the list_all limit=10000 path)

Acceptance criteria covered
----------------------------
* AC-3 (embed_ms=0 stored explicitly; DB+format time is the benchmark figure)
* AC-5 (``@pytest.mark.benchmark_pr``)
* AC-6 (skips via ``db_pool``)
"""

from __future__ import annotations

import uuid

import pytest

from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.store import MemoryStore
from benchmarks.common.embedding_providers import HashingEmbeddingProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EMBED_DIM = 1536
_THREAD_LENGTHS: dict[str, int] = {
    "turns10":   10,
    "turns100":  100,
    "turns1000": 1_000,
}

_ROLE_CYCLE = ["user", "assistant"]
_CONTENT_TEMPLATES = [
    "Turn content for thread-length benchmark: agent explains concept number {i}.",
    "User asks follow-up question number {i} about memory and retrieval.",
    "Assistant clarifies the relationship between working and episodic memory {i}.",
]


# ---------------------------------------------------------------------------
# Helper: build a fixture for a given thread length
# ---------------------------------------------------------------------------


def _make_summary_fixture(length_label: str):
    """Return a module-scoped pytest fixture that seeds a thread of n turns."""

    @pytest.fixture(scope="module")
    def _fixture(db_pool):  # type: ignore[no-untyped-def]
        n_turns = _THREAD_LENGTHS[length_label]
        provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
        store = MemoryStore(
            pool=db_pool,
            embedding_provider=provider,
            embedding_dim=_EMBED_DIM,
            enable_chunking=False,
        )
        run_id = uuid.uuid4().hex[:12]
        scope = MemoryScope(
            tenant_id=f"bm9-summary-{length_label}-{run_id}",
            agent_id=f"bm9-summary-agent-{length_label}-{run_id}",
            thread_id=f"bm9-summary-thread-{length_label}-{run_id}",
        )
        # Seed n_turns working-memory rows
        messages = [
            {
                "role": _ROLE_CYCLE[i % 2],
                "content": _CONTENT_TEMPLATES[i % len(_CONTENT_TEMPLATES)].format(i=i),
            }
            for i in range(n_turns)
        ]
        store.add_messages(messages, scope)
        yield store, scope, n_turns
        store.erase_all(scope)

    return _fixture


# Materialise the three fixtures at module level.
summary_turns10 = _make_summary_fixture("turns10")
summary_turns100 = _make_summary_fixture("turns100")
summary_turns1000 = _make_summary_fixture("turns1000")


# ---------------------------------------------------------------------------
# B12: get_summary() benchmarks per thread length
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_get_summary_10_turns(benchmark, summary_turns10):  # type: ignore[no-untyped-def]
    """B12a: get_summary() on a 10-turn thread (baseline / near-zero fetch)."""
    store, scope, n_turns = summary_turns10
    _run_summary_benchmark(benchmark, store, scope, n_turns, label="turns10")


@pytest.mark.benchmark_pr
def test_get_summary_100_turns(benchmark, summary_turns100):  # type: ignore[no-untyped-def]
    """B12b: get_summary() on a 100-turn thread."""
    store, scope, n_turns = summary_turns100
    _run_summary_benchmark(benchmark, store, scope, n_turns, label="turns100")


@pytest.mark.benchmark_pr
def test_get_summary_1000_turns(benchmark, summary_turns1000):  # type: ignore[no-untyped-def]
    """B12c: get_summary() on a 1 000-turn thread — exercises full fetch + format cost."""
    store, scope, n_turns = summary_turns1000
    _run_summary_benchmark(benchmark, store, scope, n_turns, label="turns1000")


# ---------------------------------------------------------------------------
# Shared runner
# ---------------------------------------------------------------------------


def _run_summary_benchmark(benchmark, store, scope, n_turns, label):  # type: ignore[no-untyped-def]
    """Benchmark get_summary() and record key metadata in extra_info."""
    # No embedding call — embed_ms is 0 (stored for AC-3 chart consistency).
    benchmark.extra_info["embed_ms"] = 0
    benchmark.extra_info["thread_length"] = n_turns
    benchmark.extra_info["label"] = label
    # Note: get_summary() does DB fetch + Python string formatting in one call.
    # The benchmark therefore measures DB + format time combined.
    benchmark.extra_info["note"] = "DB_fetch_plus_python_format_combined"

    def _get_summary():
        return store.get_summary(scope=scope)

    summary = benchmark(_get_summary)
    assert summary is not None
    assert isinstance(summary.content, str)
    benchmark.extra_info["message_count"] = summary.message_count
    benchmark.extra_info["truncated"] = summary.truncated


# ---------------------------------------------------------------------------
# B12d: get_summary() with token_budget
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_get_summary_with_token_budget(benchmark, summary_turns1000):  # type: ignore[no-untyped-def]
    """B12d: get_summary() on a 1 000-turn thread with a token_budget constraint.

    Verifies that the token-budget truncation code path is exercised
    (truncated=True expected) and doesn't add material overhead.
    """
    store, scope, n_turns = summary_turns1000

    benchmark.extra_info["embed_ms"] = 0
    benchmark.extra_info["thread_length"] = n_turns
    benchmark.extra_info["token_budget"] = 500

    def _get_summary():
        return store.get_summary(scope=scope, token_budget=500)

    summary = benchmark(_get_summary)
    assert summary is not None
    assert summary.truncated, (
        f"Expected truncated=True with token_budget=500 and {n_turns} turns"
    )
    benchmark.extra_info["message_count"] = summary.message_count

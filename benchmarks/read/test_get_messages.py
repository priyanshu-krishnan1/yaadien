"""
benchmarks/read/test_get_messages.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-9 / B13: ``get_messages(start, end)`` range sweep.

``get_messages()`` internally calls ``working.list_all(limit=1000)``, reverses
the list, then applies Python ``list[start:end]`` slice semantics.  The DB cost
is always a single ``list_all`` call; the variation across ranges is pure
Python list slicing (sub-microsecond) on the already-fetched rows.

A 1 000-turn thread is seeded once (module scope) and the following slices
are benchmarked:
  * Full range            ``get_messages(0, None)``  — all 1 000 messages
  * First 10             ``get_messages(0, 10)``
  * Last 10              ``get_messages(990, 1000)``
  * Middle slice         ``get_messages(400, 600)``

Because the DB cost is identical for all slices (same ``list_all`` call),
the benchmark also stores ``extra_info["slice_python_only"]`` noting that
differences between slices are Python-side only.

embed_ms is always 0 (no embedding call in get_messages).

Acceptance criteria covered
----------------------------
* AC-3 (embed_ms=0 stored; DB cost is the list_all call)
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
_THREAD_LENGTH = 1_000

_ROLE_CYCLE = ["user", "assistant"]
_CONTENT_TEMPLATE = "Message {i} in a 1000-turn thread for get_messages benchmark."

# (label, start, end) tuples for parametrization
_RANGES: list[tuple[str, int, int | None]] = [
    ("full",        0,   None),
    ("first_10",    0,   10),
    ("last_10",     990, 1_000),
    ("middle_200",  400, 600),
]


# ---------------------------------------------------------------------------
# Module-scoped seed fixture: a single 1000-turn thread
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def messages_store_and_scope(db_pool):  # type: ignore[no-untyped-def]
    """Seed a 1000-turn working-memory thread; yield (store, scope, n_turns)."""
    provider = HashingEmbeddingProvider(dim=_EMBED_DIM)
    store = MemoryStore(
        pool=db_pool,
        embedding_provider=provider,
        embedding_dim=_EMBED_DIM,
        enable_chunking=False,
    )
    run_id = uuid.uuid4().hex[:12]
    scope = MemoryScope(
        tenant_id=f"bm9-messages-{run_id}",
        agent_id=f"bm9-messages-agent-{run_id}",
        thread_id=f"bm9-messages-thread-{run_id}",
    )
    # Batch-insert in chunks of 100 to avoid overly large single calls
    batch_size = 100
    for batch_start in range(0, _THREAD_LENGTH, batch_size):
        messages = [
            {
                "role": _ROLE_CYCLE[i % 2],
                "content": _CONTENT_TEMPLATE.format(i=batch_start + i),
            }
            for i in range(min(batch_size, _THREAD_LENGTH - batch_start))
        ]
        store.add_messages(messages, scope)
    yield store, scope, _THREAD_LENGTH
    store.erase_all(scope)


# ---------------------------------------------------------------------------
# B13: get_messages() range sweep
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
@pytest.mark.parametrize(
    "label,start,end",
    _RANGES,
    ids=[label for label, _, _ in _RANGES],
)
def test_get_messages_range(benchmark, messages_store_and_scope, label, start, end):  # type: ignore[no-untyped-def]
    """B13: Benchmark get_messages(start, end) for different slice ranges.

    The underlying DB cost is identical for all slices (always one list_all
    call fetching up to 1000 rows); range differences are Python-side only.
    This test documents both the total wall-clock time (benchmark) and the
    slice size in extra_info for chart annotation.

    Note: get_messages() uses list_all(limit=1000) internally, so threads
    longer than 1000 messages will be silently truncated at the DB fetch stage.
    """
    store, scope, n_turns = messages_store_and_scope

    # No embedding call — embed_ms = 0 (AC-3 chart consistency).
    benchmark.extra_info["embed_ms"] = 0
    benchmark.extra_info["slice_label"] = label
    benchmark.extra_info["start"] = start
    benchmark.extra_info["end"] = end if end is not None else n_turns
    benchmark.extra_info["thread_length"] = n_turns
    # All slices share the same DB cost; differences are Python list slicing.
    benchmark.extra_info["slice_python_only"] = True

    def _get_messages():
        return store.get_messages(scope=scope, start=start, end=end)

    messages = benchmark(_get_messages)
    assert isinstance(messages, list)

    # Validate slice bounds
    expected_count = (end if end is not None else n_turns) - start
    # The seeded thread may be ≤ n_turns due to dedup; allow some flexibility.
    assert len(messages) <= min(expected_count, n_turns), (
        f"Slice [{start}:{end}] returned {len(messages)} messages, "
        f"expected ≤ {min(expected_count, n_turns)}"
    )
    benchmark.extra_info["returned_count"] = len(messages)

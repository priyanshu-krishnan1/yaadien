"""
benchmarks/adapters/test_mcp_adapter.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-11 G3 — Adapter overhead: MCP server tools

Benchmarks the four MCP tool implementations (``remember``, ``recall``,
``forget``, ``list_memories``) against the equivalent direct ``MemoryStore``
calls to separate:

- **Latency overhead** — extra Python dispatching, argument parsing, and JSON
  serialization inside each ``_tool_*`` function.
- **Payload size** — bytes of JSON produced by the MCP response frame
  (``benchmark.extra_info["payload_bytes"]``).  Framing overhead and DB
  latency are different problems with different fixes; recording them
  separately lets readers diagnose each independently.

Each pair of tests (MCP tool vs. direct) is separate so pytest-benchmark
captures independent statistics for each.  Compare the two ``mean`` values
to derive a delta-latency; compare ``payload_bytes`` (stored in
``extra_info``) to understand framing cost.

The ``_tool_*`` functions are called directly (no running MCP server process
needed) because the server factory just registers them as ``async`` coroutines
behind the dispatcher — we call them the same way the dispatcher does.

Skip conditions (applied at module level):
  - ``mcp`` not installed → entire module skipped.
  - ``DB2_HOSTNAME`` not set → skipped via the ``db_pool`` fixture.
"""

from __future__ import annotations

import asyncio

import pytest

# Gate: skip the entire module if the mcp package is not installed.
pytest.importorskip(
    "mcp",
    reason="mcp not installed; skipping MCP adapter benchmarks (G3)",
)

# Import the internal tool functions directly — no server process needed.
from agent_memory_sdk.adapters.mcp_server import (
    _tool_forget,
    _tool_list,
    _tool_recall,
    _tool_remember,
)
from agent_memory_sdk.models import WorkingMemory
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler

pytestmark = pytest.mark.benchmark_pr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store(db_pool) -> MemoryStore:
    """Build a minimal MemoryStore for benchmarking."""
    return MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )


def _payload_bytes(results: list) -> int:
    """Return the total byte length of all TextContent responses."""
    total = 0
    for item in results:
        # mcp.types.TextContent exposes a ``text`` attribute.
        text = getattr(item, "text", "")
        total += len(text.encode("utf-8"))
    return total


# ---------------------------------------------------------------------------
# G3a — MCP ``remember`` tool vs. direct store.remember()
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_mcp_remember_tool(benchmark, db_pool, benchmark_scope):
    """G3a — MCP tool: ``_tool_remember`` (remember a working-memory row)."""
    store = _store(db_pool)
    args = {
        "agent_id": benchmark_scope.agent_id,
        "content": "benchmark mcp turn: hello world",
        "memory_type": "working",
        "thread_id": benchmark_scope.thread_id,
        "tenant_id": benchmark_scope.tenant_id,
    }

    payload_bytes_list: list[int] = []

    def _run():
        results = asyncio.run(_tool_remember(store, args))
        payload_bytes_list.append(_payload_bytes(results))
        return results

    benchmark(_run)

    # Record MCP payload size separately from latency.
    if payload_bytes_list:
        benchmark.extra_info["payload_bytes"] = (
            sum(payload_bytes_list) / len(payload_bytes_list)
        )


@pytest.mark.benchmark_pr
def test_mcp_remember_direct(benchmark, db_pool, benchmark_scope):
    """G3a — Direct: ``store.remember(WorkingMemory, scope)``."""
    store = _store(db_pool)

    def _run():
        record = WorkingMemory(
            agent_id=benchmark_scope.agent_id,
            content="benchmark mcp turn: hello world",
            metadata={},
        )
        return store.remember(record, benchmark_scope)

    benchmark(_run)


# ---------------------------------------------------------------------------
# G3b — MCP ``list_memories`` tool vs. direct store.working.list_all()
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_mcp_list_memories_tool(benchmark, db_pool, benchmark_scope):
    """G3b — MCP tool: ``_tool_list`` (list_memories without vector search)."""
    store = _store(db_pool)
    # Seed a few rows.
    for i in range(3):
        store.remember(
            WorkingMemory(
                agent_id=benchmark_scope.agent_id,
                content=f"benchmark seed {i}",
                metadata={},
            ),
            benchmark_scope,
        )

    args = {
        "agent_id": benchmark_scope.agent_id,
        "memory_type": "working",
        "limit": 20,
        "thread_id": benchmark_scope.thread_id,
        "tenant_id": benchmark_scope.tenant_id,
    }

    payload_bytes_list: list[int] = []

    def _run():
        results = asyncio.run(_tool_list(store, args))
        payload_bytes_list.append(_payload_bytes(results))
        return results

    benchmark(_run)

    if payload_bytes_list:
        benchmark.extra_info["payload_bytes"] = (
            sum(payload_bytes_list) / len(payload_bytes_list)
        )


@pytest.mark.benchmark_pr
def test_mcp_list_memories_direct(benchmark, db_pool, benchmark_scope):
    """G3b — Direct: ``store.working.list_all(scope, limit=20)``."""
    store = _store(db_pool)
    # Seed identical rows for a fair comparison.
    for i in range(3):
        store.remember(
            WorkingMemory(
                agent_id=benchmark_scope.agent_id,
                content=f"benchmark seed {i}",
                metadata={},
            ),
            benchmark_scope,
        )

    def _run():
        return store.working.list_all(scope=benchmark_scope, limit=20)

    benchmark(_run)


# ---------------------------------------------------------------------------
# G3c — MCP ``recall`` tool vs. direct store.working.list_all() fallback
#        (no embedding → recency-list fallback inside _tool_recall)
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_mcp_recall_tool_no_embedding(benchmark, db_pool, benchmark_scope):
    """G3c — MCP tool: ``_tool_recall`` without query_embedding (recency fallback).

    When ``query_embedding`` is omitted the tool falls back to a recency list —
    the same DB call as ``list_all()``, but with additional JSON serialization
    overhead.  This measures that framing cost without vector-search latency.
    """
    store = _store(db_pool)
    for i in range(3):
        store.remember(
            WorkingMemory(
                agent_id=benchmark_scope.agent_id,
                content=f"benchmark recall seed {i}",
                metadata={},
            ),
            benchmark_scope,
        )

    args = {
        "agent_id": benchmark_scope.agent_id,
        "memory_type": "working",
        "top_k": 5,
        "thread_id": benchmark_scope.thread_id,
        "tenant_id": benchmark_scope.tenant_id,
        # query_embedding intentionally omitted → recency fallback
    }

    payload_bytes_list: list[int] = []

    def _run():
        results = asyncio.run(_tool_recall(store, args))
        payload_bytes_list.append(_payload_bytes(results))
        return results

    benchmark(_run)

    if payload_bytes_list:
        benchmark.extra_info["payload_bytes"] = (
            sum(payload_bytes_list) / len(payload_bytes_list)
        )


@pytest.mark.benchmark_pr
def test_mcp_recall_no_embedding_direct(benchmark, db_pool, benchmark_scope):
    """G3c — Direct: ``store.working.list_all(scope, limit=5)`` (the recall fallback)."""
    store = _store(db_pool)
    for i in range(3):
        store.remember(
            WorkingMemory(
                agent_id=benchmark_scope.agent_id,
                content=f"benchmark recall seed {i}",
                metadata={},
            ),
            benchmark_scope,
        )

    def _run():
        return store.working.list_all(scope=benchmark_scope, limit=5)

    benchmark(_run)


# ---------------------------------------------------------------------------
# G3d — MCP ``forget`` tool vs. direct store.forget()
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_mcp_forget_tool(benchmark, db_pool, benchmark_scope):
    """G3d — MCP tool: ``_tool_forget`` (soft-delete by id).

    A fresh row is inserted before each timed call so every iteration
    actually tombstones a live row.
    """
    store = _store(db_pool)

    payload_bytes_list: list[int] = []

    def _run():
        results = asyncio.run(
            _tool_forget(
                store,
                {
                    "agent_id": benchmark_scope.agent_id,
                    "record_id": _insert_row(store, benchmark_scope),
                    "memory_type": "working",
                    "thread_id": benchmark_scope.thread_id,
                    "tenant_id": benchmark_scope.tenant_id,
                },
            )
        )
        payload_bytes_list.append(_payload_bytes(results))
        return results

    benchmark(_run)

    if payload_bytes_list:
        benchmark.extra_info["payload_bytes"] = (
            sum(payload_bytes_list) / len(payload_bytes_list)
        )


def _insert_row(store: MemoryStore, scope) -> str:
    """Helper: insert a working-memory row and return its id."""
    record = store.remember(
        WorkingMemory(
            agent_id=scope.agent_id,
            content="benchmark forget target",
            metadata={},
        ),
        scope,
    )
    return record.id


@pytest.mark.benchmark_pr
def test_mcp_forget_direct(benchmark, db_pool, benchmark_scope):
    """G3d — Direct: ``store.forget(record_id, 'working', scope)``."""
    store = _store(db_pool)

    def _run():
        row_id = _insert_row(store, benchmark_scope)
        return store.forget(row_id, "working", benchmark_scope)

    benchmark(_run)

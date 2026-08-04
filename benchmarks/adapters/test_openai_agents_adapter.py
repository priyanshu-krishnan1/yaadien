"""
benchmarks/adapters/test_openai_agents_adapter.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-11 G2 — Adapter overhead: OpenAI Agents SDK (``Db2Session``)

Benchmarks ``Db2Session.add_items()`` and ``Db2Session.get_items()`` against
the equivalent direct ``MemoryStore`` calls (``store.remember()`` and
``store.working.list_all()``) to quantify adapter overhead.

Each pair of tests (adapter vs. direct) is separate so pytest-benchmark
captures independent statistics for each.  The user compares the two
``mean`` values in the benchmark report to derive a delta-latency.

``Db2Session`` methods are async; we call them via ``asyncio.run()`` so they
work inside the synchronous pytest-benchmark harness without requiring an
event-loop fixture.

Skip conditions (applied at module level):
  - ``agents`` (openai-agents) not installed → entire module skipped.
  - ``DB2_HOSTNAME`` not set                → skipped via the ``db_pool`` fixture.
"""

from __future__ import annotations

import asyncio

import pytest

# Gate: skip the entire module if openai-agents is not installed.
# The package name is "agents" on import.
agents_mod = pytest.importorskip(
    "agents",
    reason="openai-agents not installed; skipping OpenAI Agents adapter benchmarks (G2)",
)

from agent_memory_sdk.adapters.openai_agents import Db2Session  # noqa: E402
from agent_memory_sdk.models import WorkingMemory  # noqa: E402
from agent_memory_sdk.store import MemoryStore  # noqa: E402
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler  # noqa: E402

pytestmark = pytest.mark.benchmark_pr

# Reuse a fixed message dict across write benchmarks.
_MESSAGE = {"role": "user", "content": "benchmark turn: hello world"}


# ---------------------------------------------------------------------------
# G2a — add_items adapter vs. direct store.remember()
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_openai_agents_add_items_adapter(
    benchmark,
    db_pool,
    benchmark_scope,
):
    """G2a — Adapter: ``Db2Session.add_items([message])``."""
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )
    session = Db2Session(
        store=store,
        agent_id=benchmark_scope.agent_id,
        session_id=benchmark_scope.thread_id,
        tenant_id=benchmark_scope.tenant_id,
    )

    def _run():
        asyncio.run(session.add_items([_MESSAGE]))

    benchmark(_run)


@pytest.mark.benchmark_pr
def test_openai_agents_add_items_direct(
    benchmark,
    db_pool,
    benchmark_scope,
):
    """G2a — Direct: ``store.remember(WorkingMemory, scope)``."""
    import json

    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )

    def _run():
        record = WorkingMemory(
            agent_id=benchmark_scope.agent_id,
            content=json.dumps(_MESSAGE),
            metadata={"role": _MESSAGE["role"]},
        )
        store.remember(record, benchmark_scope)

    benchmark(_run)


# ---------------------------------------------------------------------------
# G2b — get_items adapter vs. direct store.working.list_all()
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_openai_agents_get_items_adapter(
    benchmark,
    db_pool,
    benchmark_scope,
):
    """G2b — Adapter: ``Db2Session.get_items()``."""
    import json

    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )
    # Seed a few rows so the list is non-trivial.
    for i in range(3):
        store.remember(
            WorkingMemory(
                agent_id=benchmark_scope.agent_id,
                content=json.dumps({"role": "user", "content": f"seed message {i}"}),
                metadata={"role": "user"},
            ),
            benchmark_scope,
        )

    session = Db2Session(
        store=store,
        agent_id=benchmark_scope.agent_id,
        session_id=benchmark_scope.thread_id,
        tenant_id=benchmark_scope.tenant_id,
    )

    def _run():
        return asyncio.run(session.get_items())

    benchmark(_run)


@pytest.mark.benchmark_pr
def test_openai_agents_get_items_direct(
    benchmark,
    db_pool,
    benchmark_scope,
):
    """G2b — Direct: ``store.working.list_all(scope)``."""
    import json

    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )
    # Seed identical rows for a fair comparison.
    for i in range(3):
        store.remember(
            WorkingMemory(
                agent_id=benchmark_scope.agent_id,
                content=json.dumps({"role": "user", "content": f"seed message {i}"}),
                metadata={"role": "user"},
            ),
            benchmark_scope,
        )

    def _run():
        return store.working.list_all(scope=benchmark_scope, limit=1000)

    benchmark(_run)

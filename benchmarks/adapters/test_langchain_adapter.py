"""
benchmarks/adapters/test_langchain_adapter.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-11 G1 — Adapter overhead: LangChain

Benchmarks the LangChain adapter (``Db2ChatMessageHistory`` /
``Db2MemoryStore``) against the equivalent direct ``MemoryStore`` calls to
quantify the per-operation adapter overhead.

Each pair of tests (adapter vs. direct) is separate so pytest-benchmark
captures independent statistics for each.  The user compares the two
``mean`` values in the benchmark report to derive a delta-latency.

Skip conditions (applied at module level):
  - ``langchain_core`` not installed → entire module skipped.
  - ``DB2_HOSTNAME`` not set        → skipped via the ``db_pool`` fixture.
"""

from __future__ import annotations

import pytest

# Gate: skip the entire module if langchain_core is not installed.
pytest.importorskip(
    "langchain_core",
    reason="langchain_core not installed; skipping LangChain adapter benchmarks (G1)",
)

try:
    from langchain_core.messages import HumanMessage as _HumanMessage
except ImportError:
    pytest.skip("langchain_core not installed", allow_module_level=True)

from agent_memory_sdk.adapters.langchain import Db2ChatMessageHistory, Db2MemoryStore
from agent_memory_sdk.models import SemanticFact, WorkingMemory
from agent_memory_sdk.store import MemoryStore
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler

pytestmark = pytest.mark.benchmark_pr

# A single HumanMessage instance reused across all write benchmarks.
_LC_MSG = _HumanMessage(content="benchmark turn: hello world")


# ---------------------------------------------------------------------------
# G1a — add_message adapter vs. direct store.remember()
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_langchain_add_message_adapter(
    benchmark,
    db_pool,
    benchmark_scope,
):
    """G1a — Adapter: ``Db2ChatMessageHistory.add_message()``."""
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )
    history = Db2ChatMessageHistory(store=store, scope=benchmark_scope)

    def _run():
        history.add_message(_LC_MSG)

    benchmark(_run)


@pytest.mark.benchmark_pr
def test_langchain_add_message_direct(
    benchmark,
    db_pool,
    benchmark_scope,
):
    """G1a — Direct: ``store.remember(WorkingMemory, scope)``."""
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )

    def _run():
        record = WorkingMemory(
            agent_id=benchmark_scope.agent_id,
            content="benchmark turn: hello world",
            metadata={"role": "human"},
        )
        store.remember(record, benchmark_scope)

    benchmark(_run)


# ---------------------------------------------------------------------------
# G1b — messages property (list retrieval) adapter vs. direct list_all()
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_langchain_list_messages_adapter(
    benchmark,
    db_pool,
    benchmark_scope,
):
    """G1b — Adapter: ``Db2ChatMessageHistory.messages`` property (list)."""
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )
    # Seed a couple of rows so the list is non-trivial.
    for i in range(3):
        store.remember(
            WorkingMemory(
                agent_id=benchmark_scope.agent_id,
                content=f"benchmark seed message {i}",
                metadata={"role": "human", "lc_type": "HumanMessage"},
            ),
            benchmark_scope,
        )

    history = Db2ChatMessageHistory(store=store, scope=benchmark_scope)

    def _run():
        return history.messages

    benchmark(_run)


@pytest.mark.benchmark_pr
def test_langchain_list_messages_direct(
    benchmark,
    db_pool,
    benchmark_scope,
):
    """G1b — Direct: ``store.working.list_all(scope)``."""
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )
    # Seed with identical rows for a fair comparison.
    for i in range(3):
        store.remember(
            WorkingMemory(
                agent_id=benchmark_scope.agent_id,
                content=f"benchmark seed message {i}",
                metadata={"role": "human"},
            ),
            benchmark_scope,
        )

    def _run():
        return store.working.list_all(scope=benchmark_scope, limit=1000, offset=0)

    benchmark(_run)


# ---------------------------------------------------------------------------
# G1c — Db2MemoryStore.mset() adapter vs. direct store.facts repo
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_langchain_mset_adapter(
    benchmark,
    db_pool,
    benchmark_scope,
):
    """G1c — Adapter: ``Db2MemoryStore.mset([(key, value)])``."""
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )
    mem_store = Db2MemoryStore(store=store, scope=benchmark_scope, namespace="facts")

    _counter = {"n": 0}

    def _run():
        _counter["n"] += 1
        mem_store.mset([(f"bm-key-{_counter['n']}", "benchmark value")])

    benchmark(_run)


@pytest.mark.benchmark_pr
def test_langchain_mset_direct(
    benchmark,
    db_pool,
    benchmark_scope,
):
    """G1c — Direct: ``store.facts.create(SemanticFact, scope)``."""
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )

    _counter = {"n": 0}

    def _run():
        _counter["n"] += 1
        record = SemanticFact(
            agent_id=benchmark_scope.agent_id,
            content="benchmark value",
            metadata={"store_key": f"bm-key-{_counter['n']}", "namespace": "facts"},
        )
        store.facts.create(record, benchmark_scope)

    benchmark(_run)

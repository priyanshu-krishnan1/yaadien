"""
benchmarks/adapters/test_agent_framework_adapter.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
BM-11 G4 — Adapter overhead: MS Agent Framework hooks

Benchmarks ``MemoryStoreContextProvider.before_run()`` and
``MemoryStoreContextProvider.after_run()`` against the equivalent direct
``MemoryStore`` calls (``store.get_context_card()`` and ``store.remember()``)
to quantify the per-call adapter overhead introduced by the lifecycle-hook
pattern.

Because ``agent_framework`` (the real Microsoft package) is unlikely to be
installed in most benchmark environments, this module injects a minimal stub
module into ``sys.modules`` — the same technique used in
``tests/integration/test_agent_framework_integration.py``.  If the real
package *is* installed it is used as-is (no stub needed).

Skip conditions (applied at module level):
  - ``agent_framework`` not importable AND stub injection fails → skipped.
  - ``DB2_HOSTNAME`` not set → skipped via the ``db_pool`` fixture.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Inject a minimal fake ``agent_framework`` stub so the adapter can be
# instantiated without the real package.  Mirrors the pattern used in
# tests/integration/test_agent_framework_integration.py.
# ---------------------------------------------------------------------------


def _ensure_agent_framework_stub() -> None:
    """Install a minimal ``agent_framework`` stub if the real package is absent."""
    if "agent_framework" in sys.modules:
        return  # real package (or already-installed stub) — nothing to do

    class _FakeContextProvider:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _FakeHistoryProvider(_FakeContextProvider):
        pass

    fake_mod = types.ModuleType("agent_framework")
    fake_mod.ContextProvider = _FakeContextProvider  # type: ignore[attr-defined]
    fake_mod.HistoryProvider = _FakeHistoryProvider  # type: ignore[attr-defined]
    sys.modules["agent_framework"] = fake_mod


_ensure_agent_framework_stub()

# Force a fresh import of the adapter against the (possibly just-installed)
# stub so _AGENT_FRAMEWORK_AVAILABLE is True.
_AF_MOD_NAME = "agent_memory_sdk.adapters.agent_framework"
sys.modules.pop(_AF_MOD_NAME, None)

import agent_memory_sdk.adapters.agent_framework as _af_mod  # noqa: E402

if not _af_mod._AGENT_FRAMEWORK_AVAILABLE:
    pytest.skip(
        "agent_framework not available (_AGENT_FRAMEWORK_AVAILABLE=False); "
        "skipping Agent Framework adapter benchmarks (G4)",
        allow_module_level=True,
    )

from agent_memory_sdk.adapters.agent_framework import (  # noqa: E402
    MemoryStoreContextProvider,
)
from agent_memory_sdk.models import WorkingMemory  # noqa: E402
from agent_memory_sdk.store import MemoryStore  # noqa: E402
from agent_memory_sdk.types import NoOpConsolidator, NoOpReconciler  # noqa: E402

pytestmark = pytest.mark.benchmark_pr


# ---------------------------------------------------------------------------
# Fake session / context objects for before_run / after_run signatures
# ---------------------------------------------------------------------------


def _make_session(thread_id: str) -> MagicMock:
    """Return a mock session with ``.session_id`` set."""
    session = MagicMock()
    session.session_id = thread_id
    return session


def _make_context() -> MagicMock:
    """Return a mock SessionContext (extend_instructions is a no-op)."""
    context = MagicMock()
    context.extend_instructions = MagicMock()
    # Expose .messages so _extract_turn_messages can read it.
    context.messages = [{"role": "user", "text": "benchmark hook message"}]
    return context


# ---------------------------------------------------------------------------
# G4a — before_run adapter vs. direct store.get_context_card()
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_agent_framework_before_run_adapter(benchmark, db_pool, benchmark_scope):
    """G4a — Adapter: ``MemoryStoreContextProvider.before_run()``."""
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )
    provider = MemoryStoreContextProvider(
        store=store,
        agent_id=benchmark_scope.agent_id,
        max_turns=20,
    )
    session = _make_session(benchmark_scope.thread_id or "bm-thread")
    context = _make_context()
    state: dict[str, Any] = {
        "thread_id": benchmark_scope.thread_id,
        "tenant_id": benchmark_scope.tenant_id,
        # no query_embedding → facts search skipped (avoids polluting the
        # benchmark with a vector-search cost unrelated to hook overhead)
    }

    def _run():
        asyncio.run(
            provider.before_run(
                agent=None,
                session=session,
                context=context,
                state=state,
            )
        )

    benchmark(_run)


@pytest.mark.benchmark_pr
def test_agent_framework_before_run_direct(benchmark, db_pool, benchmark_scope):
    """G4a — Direct: ``store.get_context_card(scope, max_turns=20)``."""
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )

    def _run():
        return store.get_context_card(benchmark_scope, max_turns=20)

    benchmark(_run)


# ---------------------------------------------------------------------------
# G4b — after_run adapter vs. direct store.remember()
# ---------------------------------------------------------------------------


@pytest.mark.benchmark_pr
def test_agent_framework_after_run_adapter(benchmark, db_pool, benchmark_scope):
    """G4b — Adapter: ``MemoryStoreContextProvider.after_run()``."""
    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )
    provider = MemoryStoreContextProvider(
        store=store,
        agent_id=benchmark_scope.agent_id,
    )
    session = _make_session(benchmark_scope.thread_id or "bm-thread")
    context = _make_context()
    state: dict[str, Any] = {
        "thread_id": benchmark_scope.thread_id,
        "tenant_id": benchmark_scope.tenant_id,
        # after_run reads messages from context.messages (set in _make_context)
    }

    def _run():
        asyncio.run(
            provider.after_run(
                agent=None,
                session=session,
                context=context,
                state=state,
            )
        )

    benchmark(_run)


@pytest.mark.benchmark_pr
def test_agent_framework_after_run_direct(benchmark, db_pool, benchmark_scope):
    """G4b — Direct: ``store.remember(WorkingMemory, scope)``."""
    import json

    store = MemoryStore(
        pool=db_pool,
        consolidator=NoOpConsolidator(),
        reconciler=NoOpReconciler(),
        enable_chunking=False,
    )
    msg = {"role": "user", "text": "benchmark hook message"}

    def _run():
        record = WorkingMemory(
            agent_id=benchmark_scope.agent_id,
            content=json.dumps(msg),
            metadata={"role": msg["role"]},
        )
        store.remember(record, benchmark_scope)

    benchmark(_run)

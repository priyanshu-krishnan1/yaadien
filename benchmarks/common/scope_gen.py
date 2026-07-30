"""
benchmarks/common/scope_gen.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Synthetic tenant/agent/user/thread id generation shared by all three suites.

Every benchmark run uses a fresh UUID-suffixed run_id so repeated runs never
collide with data left behind by a previous run (there is no teardown —
benchmark data is real rows in the target Db2 instance, left in place for
inspection; re-run against a scratch database if you want a clean slate).
"""

from __future__ import annotations

import uuid

from agent_memory_sdk.models import MemoryScope


def new_run_id() -> str:
    """A short id identifying one benchmark invocation, embedded in every
    synthetic agent_id/tenant_id so rows from different runs are trivially
    distinguishable (and never collide)."""
    return uuid.uuid4().hex[:12]


def make_scope(
    run_id: str,
    tenant_index: int,
    agent_index: int,
    user_index: int | None = None,
    thread_index: int | None = None,
) -> MemoryScope:
    """Build a deterministic-per-index, run-unique :class:`MemoryScope`.

    Args:
        run_id:        Value from :func:`new_run_id`, shared across a run.
        tenant_index:  Synthetic tenant number.
        agent_index:   Synthetic agent number within the tenant.
        user_index:    Optional synthetic user number.
        thread_index:  Optional synthetic thread number.
    """
    scope_kwargs: dict = {
        "tenant_id": f"bench-{run_id}-tenant-{tenant_index}",
        "agent_id": f"bench-{run_id}-tenant-{tenant_index}-agent-{agent_index}",
    }
    if user_index is not None:
        scope_kwargs["user_id"] = f"bench-{run_id}-user-{user_index}"
    if thread_index is not None:
        scope_kwargs["thread_id"] = f"bench-{run_id}-thread-{thread_index}"
    return MemoryScope(**scope_kwargs)


def marker_for(scope: MemoryScope) -> str:
    """A unique token embedded in synthetic content so leakage across scopes
    (isolation-under-load suite) is trivially detectable by substring search:
    a result containing another scope's marker is unambiguous cross-scope
    leakage, not a false positive from semantically similar content."""
    return f"[[MARKER:{scope.tenant_id}:{scope.agent_id}]]"

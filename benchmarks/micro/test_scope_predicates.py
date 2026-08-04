"""Micro-benchmarks for hierarchical scope predicate building."""

from __future__ import annotations

import pytest

from agent_memory_sdk.models import MemoryScope
from agent_memory_sdk.repositories.base import _scope_predicates


@pytest.mark.benchmark_micro
@pytest.mark.parametrize(
    ("scope_name", "scope"),
    [
        ("minimal", MemoryScope(agent_id="agent-1")),
        ("partial", MemoryScope(agent_id="agent-1", tenant_id="tenant-1")),
        (
            "full",
            MemoryScope(
                agent_id="agent-1",
                tenant_id="tenant-1",
                user_id="user-1",
                thread_id="thread-1",
            ),
        ),
    ],
)
def test_scope_predicates(
    benchmark: pytest.BenchmarkFixture,
    scope_name: str,
    scope: MemoryScope,
) -> None:
    """Benchmark predicate construction for minimal, partial, and full scopes."""
    benchmark.extra_info["scope"] = scope_name
    benchmark(_scope_predicates, scope)
